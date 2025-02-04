import numpy as np

class Continuation:
    """
    A class for doing continuation with respect to a parameter of a nonlinear
    problem.
    
    Parameters
    ----------
    solver : tmdsimpy.NonlinearSolver or similar 
        Object with routines for linear and nonlinear solutions.
        This object has settings that govern how nonlinear solutions are solved
        and when they are treated as converged.
    ds0 : float, optional
        Size of first step. If `ds0` is included in `config`, this value is
        overridden, but may still be used to calculate `dsmin` and `dsmax`.
        The default is 0.01.
    CtoP : (N+1) numpy.ndarray or None, optional
        Scaling vector to convert from conditioned space `XlamC` to physical 
        coordinates `XlamP` as `XlamP = CtoP*XlamC`. 
        If None, the vector is set to be ones on the first continuation call.
        Vector may change during continuation if dynamic conditioning is on.
        The default is None.
    RPtoC : (N+1) numpy.ndarray or None, optional
        This is a conditioning vector or scalar applied to scale the residual.
        If None, the vector defaults to 1. 
        If dynamic conditioning is used, this vector will be replaced with 
        a scalar at every step.
        The default is None.
    config : Dictionary of settings that are all optional, optional.
                FracLam : float, optional
                    Fraction of importance of `lamda` in arclength. 
                    1=lambda control, 0='displacement (`X`)' control, 
                    The default is 0.5.
                ds0 : float, optional
                    Initial step size. This overrides the input `ds0`.
                    The default is the input parameter `ds0`.
                dsmax : float, optional
                    Maximum step size allowed for any step.
                    The default is `5*ds0`.
                dsmin : float, optional
                    Minimum step size allowed.
                    The default is `ds0/5`.
                MaxSteps : int, optional
                    Maximum number of allowed solution points in the 
                    continuation.
                    The default is 500.
                TargetFeval : int, optional
                    Target number of function evaluations for each step
                    used to adaptively adjust step size.
                    The default is 20.
                DynamicCtoP : bool, optional
                    If True, the `CtoP` vector is dynamically updated for each 
                    continuation step. The initial value of `CtoP` is used as 
                    a minimum value of `CtoP` for any step, but `CtoP` can 
                    increase
                    for some variables (each element independently). 
                    Dynamic conditioning is also applied to the residual vector
                    (`RPtoC`), and converts it to a scalar.
                    The default is False.
                verbose : int, optional
                    Number of steps to output updates at.
                    If less than 0, all output is supressed.
                    If 0, some output is still printed.
                    The default is 100.
                xtol : float or None, optional
                    This tolerance is passed to the solver as
                    `solver.nsolve(xtol=xtol)`
                    The default is None.
                corrector : {'Ortho', 'Pseudo'}, optional
                    Option for the continuation constraint equation. 
                    'Ortho' requires that the correction be orthogonal to the 
                    prediction. 
                    'Pseudo' requires that a norm (in conditioned space)
                    of the solution minus the previous solution be a fixed
                    value.
                    The default is Ortho. 
                FracLamList : list, optional
                    List of FracLam values to try if the initial value of 
                    FracLam fails at a step at the minimum step size.
                    If FracLam is the first value in
                    this list, then the first value of this list is ignored.
                    The default is [].
                backtrackStop : float, optional
                    If continuation starts backtracking by 
                    more than this amount past the start value 
                    it will end before taking the maximum 
                    number of steps. Has not been fully tested.
                    The default is numpy.inf.
                MaxIncrease : float, optional
                    The maximum factor that will multiply the continuation 
                    step between two consecutive steps regardless of how
                    few interations compared to `TargetFeval` are required
                    for the nonlinear solver.
                    Step size does not have any limits on how fast it can
                    decrease.
                    The default is 1.2.
                nsolve_verbose : int, optional
                    Setting passed to solver as
                    `solver.nsolve(verbose=nsolve_verbose)`
                    The default is False (0).
                callback : function or None, optional
                    Function that is called after every solution. 
                    function is passed arguments of `XlamP,dirP_prev`
                    corresponding to the solution at the current
                    point and the prediction direction that was
                    used to calculate that point. Function is
                    called after initial solution with `np.nan`
                    vector of `dirP_prev` and twice after the final
                    converged solution. The final call has `np.nan`
                    vector for `XlamP`, and has `dirP_prev` if one was to 
                    take another step (correponds to slope at final
                    solution) for interpolation.
                    Both of the arguments are in physical coordinates (not 
                    conditioned space).
                    The default is None.
    
    See Also
    --------
    NonlinearSolver :
        Class for first required input.
    VibrationSystem :
        Class with several residual functions that can be solved with 
        continuation.
    tmdsimpy.utils.continuation : 
        Module of functions to aid in continuation including callback
        functions.
    continuation : 
        Function for doing continuation of the problem. Documation includes
        more details for settings to change if continuation fails.

    Notes
    -----
    A new continuation object needs to be initialized if the size (number of 
    unknowns) of the problem being solved changes because `CtoP` must match
    the number of unknowns.
    
    Dynamic scaling of the residual vector means that `NonlinearSolver`
    tolerances based on the residual value are difficult to determine.
    It is recommended to avoid such tolerances.
    
    Terminology:
    
        X : (N,) numpy.ndarray
            General vector of unknowns
        lam : float
            (lambda), control variable that continuation is following 
            (e.g., amplitude for EPMC, frequency for HBM).
        Xlam : (N+1,) numpy.ndarray
            Vector of `X` with `lam` appended to it.
        C : char
            Variables in conditioned space, should all be Order(1). 
            Solutions are calculated in this space.
        P : char
            Variables in physical space. These are the solutions of 
            interest.
        fun : function
            function for evaluations, all are done using physical coordinates 
            and conditioning is handled in this class.

    """
    
    def __init__(self, solver, ds0=0.01, CtoP=None, RPtoC=None, config={}):
        
        self.solver = solver
        
        if CtoP is None:
            self.setCtoPto1 = True
        else:
            assert len(CtoP.shape) == 1, \
                'Conditioning vector is expected to be 1D'
                
            self.setCtoPto1 = False
            self.CtoP = np.abs(CtoP)
            
        if RPtoC is None:
            # Needed vector for using RPtoC in initial solve
            self.setRPtoCto1 = True
            
            # General backup of use 1 to do nothing in RPtoC
            self.RPtoC = 1
        else:
            self.RPtoC = RPtoC
            self.setRPtoCto1 = False
            
        default_config={'FracLam' : 0.5, 
                        'ds0' : ds0,
                        'dsmax' : 5*ds0, 
                        'dsmin' : ds0/5,
                        'MaxSteps' : 500,
                        'TargetNfev': 20, 
                        'DynamicCtoP': False,
                        'verbose' : 100, # print frequency
                        'xtol'    : None, 
                        'corrector': 'Ortho', # Pseudo or Ortho
                        'FracLamList' : [], # FracLam options
                        'backtrackStop': np.inf, # Limits backtracking
                        'MaxIncrease': 1.2, # maximum step increase over 1 step
                        'nsolve_verbose' : False,
                        'callback' : None,
                        'CtoPsave' : None
                        }
        
        
        for key in config.keys():
            default_config[key] = config[key]
            
        # Make sure to always start with the value of 'FracLam' that is passed 
        # in before proceeding to the list of other possible values. 
        if len(default_config['FracLamList']) == 0 \
            or not (default_config['FracLamList'][0] == default_config['FracLam']):
            
            default_config['FracLamList'].insert(0, default_config['FracLam'])
            
        self.config = default_config
        
        
    def predict(self, fun, XlamP0, XlamPprev, dirC_prev):
        """
        Predicts the direction of the next step with the correct sign and ds=1.
        
        This function may be useful in additional analysis, but does not need
        to be directly called to do a continuation.
        
        Parameters
        ----------
        fun : function
            Function that continuation is following that produces N residual 
            values given the `XlamP`.
            `fun` returns `(R,dRdX,dRdlam)`. 
            Here `R` is the `(N,) numpy.ndarray` residual vector.
            `dRdX` is the `(N,N) numpy.ndarray` for derivative of `R` with
            respect to `X`.
            `dRdlam` is the derivative of `R` with respect to `lam`.
        XlamP0 : (N+1,) numpy.ndarray 
            Physical coordinates followed by lam value. 
            Previous converged solution. Prediction is from this point towards
            a new point.
        XlamPprev : (N+1,) numpy.ndarray
            The start of the previous step (converged solution before 
            `XlamP0`).
            This value is used to determine which sign of prediction continues
            in the same direction.
        dirC_prev : (N+1,) numpy.ndarray
            Predicted direction from the previous step (from `XlamPprev`).
            This helps ease the calculation, but in general does not change
            the result.

        Returns
        -------
        dirC : numpy.ndarray
            Direction vector scaled to be a step size of ds = 1
            Vector is signed to be consistent with the direction between the 
            previous two solutions.
        
        See Also
        --------
        continuation : 
            Function that conducts full continuation.
            
        Notes
        -----
        
        1. This function currently solves an (N+1, N+1) linear system to find
        an appropriate null-space vector for the top (N, N+1) matrix. This 
        allows for using the same linear solvers with parallel options as 
        the nonlinear solver, but may not be ideal for using few continuation
        steps around sharp turning points.
        Precisely, if `dirC` should be orthogonal to `dirC_prev`, then this
        approach for calculating the null-space will likely fail.
        However, this is very unlikely to happen. Furthermore, for an
        orthogonal corrector, a step where the next prediction should
        be orthogonal to the previous prediction would likely fail to solve
        making such an issue even more unlikely.

        2. If multiple FracLam values are used in the case of nonconvergence, 
        then this function is repeatedly called, but those later calls should
        not have to re-evaluate the residual and re-find the null space since
        it has not changed. Therefore, this could be sped up by eliminating
        that work on repeat calls at the same XlamP0 value.

        """
        
        R, dRdXP, dRdlamP = fun(XlamP0)
        
        # Augment the residual gradient with an additional equation 
        # corresponding to an orthogonal constraint from the previous step
        # dirC_prev is used here rather than (XlamP0 - XlamPprev) because
        # for an orthogonal corrector, it is much harder for a previous
        # step to result in a point with a tangent orthogonal to dirC_prev.
        
        # Conditioned space, (N+1,N+1) ndarray for augmented residual
        dRdXlamC = np.vstack((np.hstack((dRdXP*self.CtoP[:-1], 
                                 np.atleast_2d(dRdlamP).T*self.CtoP[-1])),
                              dirC_prev))
        
        # Want to still satisfy the first N equations described by fun
        predictR = np.zeros(R.shape[0]+1)
        
        # Want to deliberately violate the arc length condition of previous 
        # step because this is to take a new step.
        predictR[-1] = 1.0
        
        dirC = self.solver.lin_solve(dRdXlamC, predictR)
        
        
        # Arc Length Weighting Parameters
        b = self.config['FracLam']
        XC0 = XlamP0[:-1] / self.CtoP[:-1]
        
        # could store norm to eliminate an O(N) calculation each iteration
        c = (1-b) / np.linalg.norm(XC0)**2 
        
        # Scale Direction so that it takes a step size of ds=1
        step_sq = c*np.linalg.norm(dirC[:-1])**2 + b*dirC[-1]**2
        
        dirC = dirC / np.sqrt(step_sq)
        
        # Set the sign to be the correct direction based on the previous step
        # Use the same inner product space as the arclength to check the sign
        
        # Comparing in the current conditioned space
        dXlamCprev = (XlamP0 - XlamPprev) / self.CtoP 
        
        signarg = c*dirC[:-1] @ dXlamCprev[:-1] + b*dirC[-1]*dXlamCprev[-1]
        
        sign = np.sign(signarg)
        
        if sign == 0:
            # choose direction arbitrarily if perfectly orthogonal
            # could use dirC_prev to choose the sign here instead, but it is 
            # extremely unlikely that true orthogonality would be hit in 
            # practice
            sign = 1 
        
        dirC = dirC * sign
        
        # Dynamic Scaling of Residual Vector
        if self.config['DynamicCtoP']:
            diagdRdX = np.diag(dRdXlamC)
            self.RPtoC = 1/np.max(np.abs(diagdRdX[:-1]))
        else:
            # Currently, code does not support vector RPtoC after this point, 
            # so have to condense RPtoC to a scalar here for all cases.
            self.RPtoC = np.mean(self.RPtoC)
        
        return dirC
        
    def pseudo_arc_res(self, XlamC, XlamC0, ds, b, c):
        """
        Method calculates the scalar residual for the pseudo-arclength 
        corrector equation.

        This function may be useful in additional analysis, but does not need
        to be directly called to do a continuation.

        Parameters
        ----------
        XlamC : (N+1,) numpy.ndarray
            Conditioned space coordinates followed by conditioned space lam.
            This is the current assumed solution that the residual is being
            evaluated for.
        XlamC0 : (N+1,) numpy.ndarray
            Conditioned space coordinates followed by conditioned space lam.
            This is the previous converged solution.
        ds : float
            Current continuation step size.
        b : float
            Scaling on `lam` part of inner product norm for measuring distance.
            This is equivalent to the `config['FracLam']` setting.
        c : float
            Scaling on the `X` part of the inner product norm for measuring
            distance.

        Returns
        -------
        Rarc : float
            Residual for the arclength equation.
        dRarcdXlamC : (N+1,) numpy.ndarray
            Derivative of `Rarc` with respect to `XlamC`.

        See Also
        --------
        continuation : 
            Function that conducts full continuation.
        
        """
        
        # Step Sizes in conditioned space
        dlamC = XlamC[-1] - XlamC0[-1]
        dXC   = XlamC[:-1] - XlamC0[:-1]
        
        #dstep_sq - step size squared
        dstep_sq = c*np.linalg.norm(dXC)**2 + b*dlamC**2
        
        dstep_sq_dXC   = 2*c*dXC
        dstep_sq_dlamC = 2*b*dlamC
        
        Rarc =  (dstep_sq - ds**2)/ds**2
        
        dRarcdXlamC = np.hstack((dstep_sq_dXC,dstep_sq_dlamC))/ds**2
        
        return Rarc, dRarcdXlamC
    
    def orthogonal_arc_res(self, XlamC, XlamC0, dirC, ds, b, c):
        """
        Method calculates the scalar residual for the orthogonal corrector
        equation.

        This function may be useful in additional analysis, but does not need
        to be directly called to do a continuation.

        Parameters
        ----------
        XlamC : (N+1,) numpy.ndarray
            Conditioned space coordinates followed by conditioned space lam.
            This is the current assumed solution that the residual is being
            evaluated for.
        XlamC0 : (N+1,) numpy.ndarray
            Conditioned space coordinates followed by conditioned space lam.
            This is the previous converged solution.
        ds : float
            Current continuation step size.
        b : float
            Scaling on `lam` part of inner product norm for measuring distance.
            This is equivalent to the `config['FracLam']` setting.
        c : float
            Scaling on the `X` part of the inner product norm for measuring
            distance.

        Returns
        -------
        Rarc : float
            Residual for the arclength equation.
        dRarcdXlamC : (N+1,) numpy.ndarray
            Derivative of `Rarc` with respect to `XlamC`.

        See Also
        --------
        continuation : 
            Function that conducts full continuation.

        """
        
        # Current Point minus Position of the predictor
        dXlamC = XlamC - (XlamC0 + dirC*ds)
        
        # 1. Dot product is in the same inner product form as the arc length 
        # predictor
        # 2. If was at point 2*ds*dirC, dXlamC=ds*dirC, 
        # Inner product of dirC with itself is 1. Divide by ds so this case 
        # would have residual O(1)
        Rarc = (c*(dXlamC[:-1] @ dirC[:-1]) + b*dXlamC[-1]*dirC[-1])/ds
        
        dRarcdXlamC = np.hstack((c*dirC[:-1], b*dirC[-1]))/ds
        
        return Rarc, dRarcdXlamC
    
    def correct_res(self, fun, XlamC, XlamC0, ds, dirC=None, calc_grad=True):
        """
        Corrector residual function for system augmented with continuation
        equation.

        Parameters
        ----------
        fun : function
            Function that continuation is following that produces N residual 
            values given the `XlamP`.
            `fun` returns `(R,dRdX,dRdlam)`. 
            Here `R` is the `(N,) numpy.ndarray` residual vector.
            `dRdX` is the `(N,N) numpy.ndarray` for derivative of `R` with
            respect to `X`.
            `dRdlam` is the derivative of `R` with respect to `lam`.
        XlamC : (N+1,) numpy.ndarray
            Test solution at the current point to evaluate residual at.
            Solution displacements then lam value in conditioned space.
        XlamC0 : (N+1,) numpy.ndarray
            Solution at the previous point in conditioned space.
        ds : float
            Current Arc length step size.
        dirC : (N+1,) numpy.ndarray, optional
            Direction of the predictor step in conditioned space, 
            only needed for orthogonal corrector.
            The default is None.
        calc_grad : bool, optional
            Flag to calculate the gradient of the residual function and 
            arc length residual.
            The default is True.

        Returns
        -------
        Raug : (N+1,) numpy.ndarray
            Residual vector with augmented equation for the arc length 
            constraint.
            Always returned as the first entry in a tuple.
        dRaugdXlamC : (N+1,N+1) numpy.ndarray
            Gradient in conditioned space for the augmented residual. 
            Only returned as second entry in tuple if calc_grad=True.

        """
        XlamP = XlamC * self.CtoP
        
        if calc_grad:
            R, dRdXP, dRdlamP = fun(XlamP)
            
            dRdXlamC = np.hstack((dRdXP*self.CtoP[:-1], 
                                  np.atleast_2d(dRdlamP).T*self.CtoP[-1]))
        
        else:
            # No Gradient Calculation
            R = fun(XlamP, calc_grad)[0]
        
        
        # Relative Weighting of variables
        b = self.config['FracLam']
        
        # could store to eliminate an O(N) calculation each iteration.
        c = (1-b) / np.linalg.norm(XlamC0[:-1])**2 
        
        if self.config['corrector'].upper() == 'PSEUDO':
            Rarc, dRarcdXlamC = self.pseudo_arc_res(XlamC, XlamC0, ds, b, c)
        elif self.config['corrector'].upper() == 'ORTHO':
            assert not (dirC is None), 'In proper call, need dirC for ortho corrector.'
            Rarc, dRarcdXlamC = self.orthogonal_arc_res(XlamC, XlamC0, dirC, ds, b, c)
        else:
            assert False, 'Invalid corrector type: '\
                + '{}'.format(self.config['corrector'].upper())
        
        # Augment R and dRdXlamC with the arc length equation
        Raug = np.hstack((self.RPtoC*R, Rarc))
        
        if calc_grad:
            dRaugdXlamC = np.vstack((self.RPtoC*dRdXlamC, dRarcdXlamC))
    
            return Raug, dRaugdXlamC
        else:
            return (Raug,)
    
    def continuation(self, fun, XlamP0, lam0, lam1, return_grad=False):
        """
        Does a continuation of the provided system of equations with respect to
        a parameter.
        
        Continuation is run from `lam0` to `lam1` with `lam` corresponding
        to the last entry of the unknowns (e.g., `UlamP0[-1]`).

        Parameters
        ----------
        fun : function
            Function that continuation is following that produces N residual 
            values given the `XlamP`.
            `fun` returns `(R,dRdX,dRdlam)`. 
            Here `R` is the `(N,) numpy.ndarray` residual vector.
            `dRdX` is the `(N,N) numpy.ndarray` for derivative of `R` with
            respect to `X`.
            `dRdlam` is the `(N,) numpy.ndarray` derivative of `R` with respect
            to `lam`.
            
            Function may need to have an optional argument `calc_grad=True`
            if the function will be used with a nonlinear solver that requires
            two input arguments. E.g. 
            'fun = lambda Xlam, calc_grad=True : residual(X, calc_grad)'.
            When `calc_grad` is False, the function should return a tuple with 
            the first entry of the tuple being `R`, the other entries may be
            ignored.
            By default, it is assumed that `fun` only takes one input. If a 
            wrapper function for continuation receives `calc_grad=False`, then
            it is assumed that `fun` will accept a second bool input.
        XlamP0 : (N+1,) numpy.ndarray
            Initial Guess (Physical Coordinates) for the solution at `lam0`.
            The N+1 entry is ignored.
        lam0 : float
            Starting value of lambda (continuation parameter).
        lam1 : float
            Desired final value of lambda.
        return_grad : bool, optional
            Flag to return the prediction directions corresponding to each 
            step.
            Currently not fully implemented/supported yet.
            This information can be captured by using a callback function.
            The default is False.

        Returns
        -------
        XlamP_full : (M, N+1) numpy.ndarray
            Final history, rows are individual entries of `XlamP` 
            (physical coordinates), and `M` steps are taken.
            
        See Also
        --------
        postprocess.continuation :
            Functions for interpolating and postporcessing continuation 
            results. 
            
        Notes 
        -----
        1. This continuation function is dependent on the object state. 
        previous calls to this function may have changed the initial state of
        conditioning vectors etc. Be aware when repeatedly calling this 
        function. Future work should make this more robust.
        
        2. `lam1` is the desired final solution point, but will not be exactly
        calculated. If continuation completes, the final solution will be past
        `lam1`. If continuation does not completely solve, then it returns what
        has been converged.
        
        Troubleshooting :
        
        So continuation failed, what should you do next? This is not 
        necessarily shocking or cause for too much alarm. It is expected that 
        one can always come up with a difficult problem to break any algorithm.
        However, by adjusting some settings you may be able to fix the issue
        and finish your continuation. The following are tips for what to try.
        
        Initial Point Fails to Converge : 
            You will need to provide a better guess or better solver settings
            to fix this. This is not an issue with continuation. 
            If using the `jax.solvers.NonlinearSolverOMP` solver, you can 
            try to use `reform_freq=1` and line search settings.
            Conditioning may not be the best for the initial solution point, 
            you can try to update your initial guess for the `CtoP` vector
            or solve the problem outside of continuation and pass that solution
            as the initial guess here.
            For vibration problems, starting in a linear regime (e.g., low
            amplitude for modal analysis or far from resonance for HBM) is more
            likely to succeed here.
        
        Fails to Solve Consistently : 
            You may be trying to take too large of steps, so try adjusting
            dsmin / dsmax / ds0. Alternatively, the solver settings may be 
            poor (e.g., `reform_freq`). If problem persists, try switching 
            between 'Ortho' and 'Pseudo' corrector types. Also, changing the 
            intial conditioning (`CtoP`) (and using dynamic conditioning) may
            improve these issues.
            
        Solver improves residual, but does not converge :
            Your solver tolerances may be unreasonable. Using relative 
            tolerances may give a sense of if the improvement is sufficient. 
            However, taking too small of steps with relative tolerances may 
            mean that the initial guess is so good that it is not possible to 
            improve it sufficiently. In those cases, use absolute tolerances.
            
        Solver converges, but steps are very small :
            If the solver is converging, but steps are much smaller than 
            expected based on the chosen value of `ds`. 
            If using dynamic scaling, a step size of `ds` should cause a change
            in `lam` of order `ds*lam`. If the intial conditioning vector has
            `CtoP[-1] = abs(lam1 - lam0)`, then the change in `lam` should be 
            of order `ds*abs(lam1 - lam0)`.
            
            This is likely caused
            by greater than expected changes in the variables other than `lam`
            and thus smaller steps than expected. One easy option would be to 
            change `FracLam` to reduce the importance of these variables. 
            However, this generally does not provide satisfactory results. 
            A better option is to provide an initial conditioning vector 
            (`CtoP`) with 
            a larger minimum value covering the variables that change the most. 
            Additionally, turn on dynamic scaling. 
            To determine the minimum value in the scaling vector, it is 
            recommend to look at a plot a solution vector on a log scale. 
            Experiment with a minimum conditioning value over a range of 
            orders of magnitude around the mean value to see what works best.
            
        Fails with very small Minimum step size:
            If the solution is repeatedly failing with minimum step size, 
            trying different values of `FracLam` (use `FracLamList`) may be 
            used to
            occasionally get around problem points. 

        No Apparent Reason :
            This happens sometimes. You can try restarting continuation exactly
            where you left off. Taking an initial step of a slightly different
            size may allow the solution to converge. Also, the prediction 
            direction when starting from a single point is not necessarily the 
            same as when starting from having two previous solutions (this
            may be most significant when illconditioning is an issue in the 
            prediction step).
            
        Failing at Sharp Turning Point : 
            An Orthogonal corrector may overshoot a sharp turn and fail. 
            Usually, an adaptive step size is sufficient to fix this. If it 
            still fails, try using the Pseudo corrector since it may work 
            better in these cases. If that still doesnt work, try running
            continuation from `lam1` to `lam0` instead. Going the opposite 
            direction may solve the problem or at least give you more of the 
            solution you are interested in.
            
        Solution starts backtracking (where it should not):
            This could be caused by either a bad prediction or a bad solve. 
            To check if it is the former, print out the `lam` value for each
            initial guess of the step. If the initial step guesses are going
            the expected direction, then the issue is a bad solve. For a bad 
            solve, try adjusting solver settings and conditioning parameters 
            (`CtoP` and dynamic scaling).
            
            If backtracking is due to the prediction choosing the wrong 
            direction, consider using a different value of FracLam. However,
            unless you go to exactly 1.0 or 0.0, this may not solve the 
            fundamental issue. Other options include decreasing the step size
            so you can resolve the feature that is causing the problem. Or you
            could try increasing the step size to just bypass the feature if 
            it is isolated. Conditioning (`CtoP`) plays a role in the 
            prediction in 
            how which sign has a consistent direction with the previous step, 
            so you may need to adjust conditioning parameters. 
            Restarting continuation from the furtherest along point may also
            succeed in continuing the same direction. 

        """
        
        assert return_grad==False, 'Have not implemented this flag yet.'
        
        # Check about removing all output
        silent = self.config['verbose'] < 0
        
        if silent:
            self.config['verbose'] = 0
        
        # Initialize Memory
        XlamP_full = np.zeros((self.config['MaxSteps'], XlamP0.shape[0]))        
        
        # Solve at Initial Point
        if not silent:
            print('Starting Continuation from ', lam0, ' to ', lam1)
        
        
        # Conditioning up front for static solution
        if self.setCtoPto1:
            self.CtoP = np.ones_like(XlamP0)
            
        if self.setRPtoCto1:
            self.RPtoC = np.ones_like(XlamP0)
            
        # Make sure that RPtoC is initially a vector for use in 
        # initial solution
        if np.atleast_1d(self.RPtoC).shape[0] == 1:
            self.RPtoC = self.RPtoC*np.ones_like(XlamP0)
            
        
        # No continuation, fixed at initial lam0
        # Not sure if fun accepts calc_grad, so will always calculate the gradient
        fun0 = lambda X, calc_grad=True : _initial_wrapper_fun(fun, X, lam0,
                                                           calc_grad=calc_grad)

        fun0_cond = self.solver.conditioning_wrapper(fun0, self.CtoP[:-1], 
                                                     RPtoC=self.RPtoC[:-1])
        
        Xc, R, dRdX, sol = self.solver.nsolve(fun0_cond, 
                                             XlamP0[:-1]/self.CtoP[:-1],
                                             xtol=self.config['xtol'], \
                                             verbose=self.config['nsolve_verbose'])
        
        # Convert back to physical coordinates
        X = Xc * self.CtoP[:-1]
                
        assert sol['success'], \
            'Failed to converge to initial point, give a better initial guess.'
        
        if not silent:
            print('Converged to initial point! Starting continuation.')
        
        if self.config['callback'] is not None:
            # Callback save of initial solution
            # dirC is not yet calculated, so passing NaN
            self.config['callback'](np.hstack((X, lam0)), 
                                    np.hstack((X, lam0))*np.nan)
                
        # Define a Reference Direction as a previous solution for use in the 
        # predictor
        direct = np.sign(lam1 - lam0)
        XlamPprev = np.hstack((X, lam0 - direct))
        
        step = 0
        XlamP0 = np.hstack((X, lam0))
        XlamP_full[step] = XlamP0
        
        # 'previous' dirC for the first step
        dirC = XlamP0 - XlamPprev
        
        step += 1
            
        if self.config['DynamicCtoP']:
            self.CtoP0 = np.copy(self.CtoP)
            
        ds = self.config['ds0']
        
        while step < self.config['MaxSteps'] \
            and direct*XlamP_full[step-1,-1] < direct*lam1 \
            and direct*XlamP_full[step-1,-1] > direct*(lam0-direct*self.config['backtrackStop']): 
            #{ Continuation step loop
            
            # Update Conditioning Dynamically
            if self.config['DynamicCtoP']:
                self.CtoP = np.maximum(np.abs(XlamP_full[step-1]), self.CtoP0)
                
            for fracLam_ind in range(len(self.config['FracLamList'])): 
                #{ fracLam loop
                
                # Select the current value of weighting lambda v. other variables
                self.config['FracLam'] = self.config['FracLamList'][fracLam_ind]
                
                # Predict Direction
                dirC = self.predict(fun, XlamP0, XlamPprev, dirC)
                                
                # Correct
                correct_fun = lambda XlamC, calc_grad=True : \
                        self.correct_res(fun, XlamC, XlamP0/self.CtoP, 
                                         ds, dirC, calc_grad=calc_grad)
                
                XlamC, R, dRdX, sol = self.solver.nsolve(correct_fun, \
                                        XlamP0/self.CtoP + dirC*ds,\
                                        xtol=self.config['xtol'],\
                                        verbose=self.config['nsolve_verbose'])
                
                # Retry with smaller steps if correction failed.
                while (not sol['success']) and ds > self.config['dsmin']:
                    ds = max(ds / 2, self.config['dsmin'])
                    
                    if self.config['verbose']:
                        print(sol['message'])
                        print('Failed to converge with ds=', 2*ds, 
                              '. Retrying with ds=', ds)
                        # print('norm(R)=', np.linalg.norm(R))
                    
                    # Correct Again
                    XlamC, R, dRdX, sol = self.solver.nsolve(correct_fun, \
                                        XlamP0/self.CtoP + dirC*ds,\
                                        xtol=self.config['xtol'],\
                                        verbose=self.config['nsolve_verbose'])
            
                # Break out of loop over FracLam values if have converged
                if sol['success']:
                    if fracLam_ind > 0 and self.config['verbose']:
                        print('Succeeded with FracLam index {} with value FracLam={}.'\
                              .format(fracLam_ind, self.config['FracLam']))
                    break
                
            #} End fracLam loop
            
            if(not sol['success'] and not silent):
                print('Stopping since final solution failed to converge.')
                break
            
            # Store Iteration and Advance
            XlamP_full[step] = self.CtoP * XlamC
            
            if self.config['verbose'] and step % self.config['verbose'] == 0:
                print('Step=', step, ' converged: lam=', XlamP_full[step, -1], \
                      ' ds=', ds, ' and nfev=', sol['nfev'])
            
            # Heuristic For updating ds
            ds = ds * min(self.config['TargetNfev'] / sol['nfev'], 
                          self.config['MaxIncrease'])
            
            ds = min(max(ds, self.config['dsmin']), self.config['dsmax'])
            
            # Callback function
            if self.config['callback'] is not None:
                self.config['callback'](XlamP_full[step], dirC*self.CtoP)
                if self.config['CtoPsave'] is not None:
                    np.savez(self.config['CtoPsave'], CtoP=self.CtoP)
            
            # Update information from previous steps
            XlamPprev = np.copy(XlamP0)
            XlamP0 = np.copy(XlamP_full[step])
            step += 1
            
        #} End Continuation step loop  
        
        # Only return solved history.
        XlamP_full = XlamP_full[:step]
        
        # Callback - save the final dirC 
        if self.config['callback'] is not None:
            if not sol['success']:
                # Pass dirC for the previous step corresponding to the last
                # converged solution
                self.config['callback'](dirC*np.nan, dirC*self.CtoP)

            else:
                # Calculate dirC for the current solution to be saved
                dirC = self.predict(fun, XlamP0, XlamPprev, dirC)
                
                self.config['callback'](dirC*np.nan, dirC*self.CtoP)
        
        if not silent:
            print('Continuation complete, at lam=', XlamP_full[step-1, -1])
            
            if step == self.config['MaxSteps']:
                print('Continuation completed due to maximum number of steps'\
                      + ' (MaxSteps={}).'.format(self.config['MaxSteps']))
        
        return XlamP_full
    

def _initial_wrapper_fun(fun, X, lam0, calc_grad=True):
    """
    Private wrapper for initial solution at lam0 point with 
    support for calc_grad=False and calc_grad=True not passed to fun

    Parameters
    ----------
    fun : function
        Residual function that is being wrapped.
    X : (N,) numpy.ndarray
        Set of unkowns in physical coordinates.
    lam0 : float
        lambda value that solution should be solved add.
    calc_grad : bool, optional
        Flag for if the gradient should be calculated. 
        The default is True.

    Returns
    -------
    tuple :
        First two return values of `fun` if `calc_grad=True`. 
        If `calc_grad=False`, then just the first entry of the return from 
        `fun`, but still in a tuple.

    """
    if calc_grad:
        return fun( np.hstack((X, lam0)) )[0:2]
    else:
        return fun( np.hstack((X, lam0)), calc_grad=False)[0:1]

