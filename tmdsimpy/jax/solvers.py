import numpy as np
import jax


from ..solvers import NonlinearSolver


class NonlinearSolverOMP(NonlinearSolver):
    """
    Nonlinear solver object that contains several functions and solver settings

    Parameters
    ----------
    config : dict, optional
        Dictionary of settings to be used in the solver (see below).

    Notes
    ----------
    
    Parallel linear and nonlinear solver functions here. 
    
    Libraries used may respond to OpenMP environment variables such as: \n
    > export OMP_PROC_BIND=spread # Spread threads out over physical cores \n
    > export OMP_NUM_THREADS=32 # Change 32 to desired number of threads

    
    config dictionary keys
    -------
    max_steps : int, default 20
        maximum number of iterations allowed in the nonlinear solver
    reform_freq : int, default 1
        Frequency of recalculating and refactoring Jacobian matrix of the
        nonlinear problem. 1 corresponds to Newton-Raphson of doing this 
        every step. Larger numbers will correspond to BFGS low rank updates
        in between steps with refactoring. 
        When reform_freq > 1, function being solved must accept the keyword
        calc_grad=True or calc_grad=False to differentiate if Jacobian 
        matrix should be calculated. If calc_grad=True, then returned tuple
        should be (R, dRdX) if False, returned tuple should start with (R,), 
        but may return other values past the 0th index of tuple.
    verbose : Boolean, default true
        Flag for if output should be printed. 
    xtol : double, default None
        Convergence tolerance on the L2 norm of the step size (dX). If None, 
        code will set the value to be equal to 1e-6*X0.shape[0] where X0 
        is the initial guess for a given solution calculation. 
        if xtol is passed to nsolve, that value is used instead
    rtol : double, default None
        convergence toleranace on the L2 norm of the residual vector (R).
    etol : double, default None
        convergence tolerance on the energy norm of the inner product of 
        step (dX) and residual (R) or e=np.abs(dX @ R)
    xtol_rel : double, default None
        convergence tolerance on norm(dX) / norm(dX_step0)
    rtol_rel : double, default None
        convergence tolerance on norm(R) / norm(R_step0)
    etol_rel : double, default None
        convergence tolerance on norm(e) / norm(e_step0)
    stopping_tol: list, default ['xtol']
        List can contain options of 'xtol', 'rtol', 'etol', 'xtol_rel', 
        'rtol_rel', 'etol_rel'. If any of the listed tolerances are 
        satisfied, then iteration is considered converged and exits. 
        Futher development would allow for the list to contain lists of 
        these same options and in a sublist, all options would be required. 
        This has not been implemented. 
    accepting_tol : list, default []
        List that can contain the same set of strings as stopping_tol. 
        Once maximum interactions has been reached, if any of these 
        tolerances are satisified by the final step, then the solution
        is considered converged. This allows for looser tolerances to be
        accepted instead of non-convergence, while still using max 
        iterations to try to achieve the tighter tolerances.
    
        """
    
    def __init__(self, config={}):
        
        default_config={'max_steps' : 20,
                        'reform_freq' : 1,
                        'verbose' : True, 
                        'xtol'    : None, 
                        'rtol'    : None,
                        'etol'    : None,
                        'xtol_rel' : None,
                        'rtol_rel' : None,
                        'etol_rel' : None,
                        'stopping_tol' : ['xtol'],
                        'accepting_tol' : []
                        }
        
        
        for key in config.keys():
            default_config[key] = config[key]
        
        self.config = default_config
        
        return
        
    def lin_solve(self, A, b):
        """
        Solve the linear system A * x = b 

        Parameters
        ----------
        A : (N,N) np.array, 2d
            Linear system matrix.
        b : (N,) np.array, 1d
            Right hand side vector.

        Returns
        -------
        x : (N,) np.array, 1d
            Solution to the linear problem

        """
        x = jax.numpy.linalg.solve(A,b)
        
        return x
    
    def lin_factor(self, A):
        """
        Factor a matrix A for later solving. This version simply stores and 
        fully solves later.

        Parameters
        ----------
        A : (N,N) np.array, 2d
            Linear system matrix for later solving.

        Returns
        -------
        lu_and_piv : tuple
            Resulting data from factoring the matrix A, can be passed to 
            self.lin_factored_solve to solve the linear system.

        """
        lu_and_piv = jax.scipy.linalg.lu_factor(A)
        
        return lu_and_piv
    
    def lin_factored_solve(self, lu_and_piv, b):
        """
        Solve the linear system with right hand side b and stored (factored)
        matrix from self.factor(A)

        Parameters
        ----------
        lu_and_piv : tuple
            results from factoring a matrix with self.lin_factor(A)
        b : (N,) np.array, 1d
            Right hand side vector.

        Returns
        -------
        x : (N,) np.array, 1d
            Solution to the linear problem

        """
        x = jax.scipy.linalg.lu_solve(lu_and_piv, b)
        
        return x
    
    def nsolve(self, fun, X0, verbose=True, xtol=None, Dscale=1.0):
        """
        Numerical nonlinear root finding solution to the problem of R = fun(X)
        
        This function uses either a full Newton-Raphson (NR) solver approach or
        Broyden-Fletcher-Goldfarb-Shanno (BFGS), which uses fewer NR iterations
        with some approximations of Jacobian between NR iterations.
        
        Solver settings are set at initialization of NonlinearSolverOMP.

        Parameters
        ----------
        fun : function handle 
            Function to be solved, function returns two arguments of R 
            (residual, (N,) numpy.ndarray) and dRdX (residual jacobian, 
            (N,N) numpy.ndarray).
            If config['reform_freq'] > 1, then fun should take two arguments
            The first is X, the second is a bool where if True, fun returns 
            a tuple of (R,dRdX). If false, fun just returns a tuple (R,)
            Function may return additional values in either tuple, but the
            additional values will be ignored here.
        X0 : (N,) numpy.ndarray
            Initial guess of the solution to the nonlinear problem.
        verbose : bool, optional
            Flag to print convergence information if True. The default is True.
        xtol : float, optional
            Tolerance to check for convergence on the step size. 
            If None, then self.config['xtol'] is used. If that is also None, 
            then 1e-6*X0.shape[0] is used as the xtolerance.
            Passing in a value here does not change the config value 
            permanently (not parallel safe though)
            The default is None. 

        Returns
        -------
        X : (N,) numpy.ndarray
            Solution to the nonlinear problem that satisfies tolerances or from
            last step.
        R : (N,) numpy.ndarray
            Residual vector from the last function evaluation (does not in 
            generally correspond to value at X to save extra evaluation of fun).
        dRdX : (N,N) numpy.ndarray
            Last residual jacobian as evaluated during solution, not at final X.
        sol : dict
            Description of final convergence state. Has keys of 
            ['message', 'nfev', 'njev', 'success']. 'success' is a bool with 
            True corresponding to convergence. 'nfev' is the number of function
            evaluations completed. 'njev' is the number of jacobian evaluations.
            'message' is either 'Converged' or 'failed'. Use the bool from 
            'success' rather than the message for decisions. 
            
            
        Other Parameters
        ----------------
        Dscale : float or numpy.ndarray, optional
            Not currently supported, this argument does nothing.
            TODO: implement:
            Value to scale X by during iteration to improve conditioning. 
            This argument is not fully tested, and is recommended to not use 
            this argument.
            The default is 1.0.

        """
        
        ##########################################################
        # Initialization
        
        # xtol support with backwards compatibility 
        if xtol is None:
            xtol = self.config['xtol']
            if xtol is None: 
                xtol = 1e-6*X0.shape[0]
            
        # Save out the setting from xtol in config, then will overwrite
        # here to update to be used for this call. 
        xtol_setting = self.config['xtol'] 
        self.config['xtol'] = xtol
        
        max_iter = self.config['max_steps']
        
        sol = {'message' : 'failed', 
               'nfev' : 0, 
               'njev' : 0,
               'success' : False}
        
        # Wrap function if using BFGS v. NR
        if self.config['reform_freq'] > 1:
            fun_R_dRdX = lambda X : fun(X, True)[0:2]
            fun_R = lambda X : fun(X, False)[0]
        else:
            fun_R_dRdX = lambda X : fun(X)[0:2]
            
        # Solution initialization
        X = X0
        
        # Previous iteration quantities, these are initialized to zero to 
        # prevent undefind variable names in python, but are redefined in the 
        # loop before they are used.
        deltaXminus1 = np.nan*np.zeros_like(X)
        Rminus1 = np.zeros_like(X)
        
        # Output printing form
        form =  '{:4d} & {: 6.4e} & {: 6.4e} & {: 6.4e} '\
                    + '& {: 6.4e} & {: 6.4e} & {: 6.4e}' \
                    + ' & {: 6.4f} & {: 6.4f} & {: 6.4f} ' \
                    + '& {:s}'
                    
        # Tracking for convergence rates
        elist = np.zeros(max_iter+1)
        rlist = np.zeros(max_iter+1)
        ulist = np.zeros(max_iter+1)
        
        rate_r = np.nan
        rate_e = np.nan
        rate_u = np.nan
            
        ##########################################################
        # Iteration Loop
        bfgs_ind = 0 # counter to check if it is time to do full NR again
        curr_iter = 'NR'
        no_nan_vals = True
        
        for i in range(max_iter):
            
            if bfgs_ind == 0: # Full Newton Update Update
                curr_iter = 'NR'
                
                R,dRdX = fun_R_dRdX(X)
                sol['nfev'] += 1
                sol['njev'] += 1
                
                if np.isnan(np.sum(R)):
                    if verbose: print('Stopping with NaN Residual')
                    no_nan_vals = False
                    break
                if np.isnan(np.sum(dRdX)):
                    if verbose: print('Stopping with NaN Jacobian')
                    no_nan_vals = False
                    break
                
                factored_data = self.lin_factor(-1.0*dRdX)
                
                deltaX = self.lin_factored_solve(factored_data, R)
                
                bfgs_v = np.zeros((X.shape[0], self.config['reform_freq']-1))
                bfgs_w = np.zeros((X.shape[0], self.config['reform_freq']-1))
                
            else: # BFGS Update        
                curr_iter = 'BFGS'

            
                import warnings
                warnings.warn('BFGS signs do not look correct yet.')
                
                R = fun_R(X)
                sol['nfev'] += 1
                
                if np.isnan(np.sum(R)):
                    if verbose: print('Stopping with NaN Residual')
                    no_nan_vals = False
                    break
            
                bfgs_v[:, bfgs_ind-1] = deltaXminus1 / (deltaXminus1 @ (R - Rminus1))
                
                alpha = np.sqrt(-deltaX @ (R - Rminus1) / (deltaX @ Rminus1))
                
                bfgs_w[:, bfgs_ind-1] = -(R - Rminus1) + alpha*Rminus1
                
                # Apply the updated jacobian to R
                deltaX = R
                for kk in range(bfgs_ind-1, -1, -1):
                    deltaX = deltaX + bfgs_w[:, kk]*(bfgs_v[:, kk] @ deltaX)
                
                deltaX = self.lin_factored_solve(factored_data, deltaX)
                
                for kk in range(0, bfgs_ind, 1):
                    deltaX = deltaX + bfgs_v[:, kk]*(bfgs_w[:, kk] @ deltaX)
               
            ###### # Update Solution
            if np.isnan(np.sum(deltaX)):
                no_nan_vals = False
                if verbose: print('Stopping with NaN Step Direction')
                break
            
            X = X + deltaX
            
            ###### # Tolerance Checking
            u_curr = np.sqrt(deltaX @ deltaX)
            e_curr = R @ deltaXminus1
            r_curr = np.sqrt(R @ R)
            
            if i == 0: # Store initial tolerances
                r0 = r_curr
                u0 = u_curr
                e0 = np.inf
                e_curr = np.inf
                
                rlist[0] = r0
                ulist[0] = u0
            if i == 1: 
                # Now have evaluated the residual so can give an initial e0
                e0 = np.abs(e_curr)
                
            elist[i] = np.sqrt(np.abs(e_curr))
            rlist[i] = r_curr
            ulist[i] = u_curr
            
            if verbose:
                if i == 0:
                    print('Iter &     |R|     &  |e_(i-1)|  &     |dU|    '\
                              + '&  |R|/|R0|   &  |e|/|e0|   &  |dU|/|dU0| ' \
                              +'&  Rate R &  Rate E &   Rate U '\
                              +'& NR/BFGS')
                
                if i >= 2:
                    # import pdb; pdb.set_trace()
                    
                    rate_r = np.log(rlist[i-1] / rlist[i]) / np.log(rlist[i-2] / rlist[i-1])
                    rate_u = np.log(ulist[i-1] / ulist[i]) / np.log(ulist[i-2] / ulist[i-1])
                    
                if i >= 3:
                    rate_e = np.log(elist[i-1] / elist[i]) / np.log(elist[i-2] / elist[i-1])
                    
                print(form.format(i, r_curr, e_curr, u_curr, 
                              r_curr/r0, e_curr/e0, u_curr/u0,
                              rate_r, rate_e, rate_u,
                              curr_iter))
            
            # Check for final convergence
            converged = _check_convg(self.config['stopping_tol'], self.config, 
                                     r_curr, e_curr, u_curr, 
                                     r_curr/r0, e_curr/e0, u_curr/u0)
            if converged:
                if verbose:
                    print('Converged!')
                sol['message'] = 'Converged'
                sol['success'] = True
                
                break
        
            ###### Setup next loop iteration
            
            # Increment and potentially reset BFGS counter
            bfgs_ind = (bfgs_ind + 1) % self.config['reform_freq']
            
            # Save R from this step since it is needed for BFGS
            Rminus1 = R
            
            # Save deltaX from this step since it is needed for calculating
            # energy norm for outputs 
            # (if energy norm goes negative, line search is useful)
            deltaXminus1 = deltaX
            

        ##########################################################
        # Final Clean Up and Return
        
        if no_nan_vals and not sol['success']:
            
            # Check convergence against the second set of tolerances
            converged = _check_convg(self.config['accepting_tol'], self.config, 
                                 r_curr, e_curr, u_curr, 
                                 r_curr/r0, e_curr/e0, u_curr/u0)
            
            if converged:
                
                if verbose:
                    print('Converged on accepting tolerances at max_iter.')
                    
                sol['message'] = 'Converged'
                sol['success'] = True

        if(verbose):
            print(sol['message'], ' Nfev=', sol['nfev'], ' Njev=', sol['njev'])
        
        # Set xtol in config back to the value passed in. 
        self.config['xtol'] = xtol_setting
        
        return X, R, dRdX, sol
    
    
def _check_convg(check_list, tol_dict, r_curr, e_curr, u_curr, r_rel, e_rel, u_rel):
    """
    Helper function to determine if convergence has been achieved. 

    Parameters
    ----------
    check_list : List
        List of tolerances to be checked. See NonlinearSolverOMP
        documentation
    tol_dict : Dictionary
        Contains tolerances and values to be checked.
    r_curr : double
        Current value of norm(R)
    e_curr : double
        Current value of e
    u_curr : double
        Current value of norm(dU)
    r_rel : double
        Current value of norm(R)/norm(R0)
    e_rel : double
        Current value of e/e0
    u_rel : double
        Current value of norm(dU)/norm(dU0)
    
    Returns
    -------
    converged : Boolean
        returns True if solution meets convergence criteria.

    """
    
    converged = False
    
    # Make dictionary of current errors
    error_dict = {
                'xtol'    : u_curr, 
                'rtol'    : r_curr,
                'etol'    : e_curr,
                'xtol_rel' : e_rel,
                'rtol_rel' : r_rel,
                'etol_rel' : u_rel,
                }
    
    for key in check_list:
        converged = converged or (np.abs(error_dict[key]) < tol_dict[key])
    
    return converged