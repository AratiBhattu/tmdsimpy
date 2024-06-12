"""
Rough Contact Friction Model utilizing JAX for Derivatives

This model is a python implementation of the work in: 
    J. H. Porter and M. R. W. Brake, 2023, Towards a predictive, physics-based 
    friction model for the dynamics of jointed structures, Mechanical Systems 
    and Signal Processing
    
This model only considers the simplest tangent model and plasticity in the 
normal direction

Code for the MATLAB version is available on GitHub: 
    https://github.com/tmd-lab/microslip-rough-contact
"""

# Standard imports
import numpy as np
import warnings


# JAX imports
import jax
import jax.numpy as jnp

# Decoractions for Partial compilation
from functools import partial

# Imports of Custom Functions and Classes
from .... import harmonic_utils as hutils
from ....jax import harmonic_utils as jhutils # Jax version of harmonic utils
from ....nlforces.nonlinear_force import NonlinearForce

# Import of functions stored in a different file
from . import _asperity_functions as asp_funs

###############################################################################
########## Class for Rough Contact Models                            ##########
###############################################################################

class RoughContactFriction(NonlinearForce):
    """
    Rough Contact friction Slider Element Nonlinearity with JAX for automatic 
    differentiation

    Parameters
    ----------
    Q : (Nnl, N) numpy.ndarray
        Transformation matrix from system DOFs (N) to nonlinear DOFs (Nn=3)
    T : (N, Nnl) numpy.ndarray
        Transformation matrix from local nonlinear forces to global 
        nonlinear forces
    ElasticMod : float
        Elastic Modulus
    PoissonRatio : float
        Poisson's Ratio
    Radius : float
        Initial Undeformed Effective Asperity Radius 
        (Half of real radius of one asperity in general [1]_)
    TangentMod : float
        Plasticity Hardening Modulus
    YieldStress : float
        Yield Strength / Yield Stress
    mu : float
        Friction Coefficient
    u0 : float, optional
        Unused option for setting initialization of friction.
        Should use method `set_aft_initialize` to set the AFT friction
        initialization point.
        The default is 0.
    meso_gap: float, optional
        Initial gap between contact due to other (e.g. mesoscale) topology.
        This gap is added to the gaps of all asperities in the integral.
        The default is 0.
    gaps : (Nasp,) numpy.ndarray
        Initial gaps between asperities that forces should be calculated
        between (excluding mesoscale topology).
    gap_weights : (Nasp,) numpy.ndarray
        Integration weights for forces between asperities with initial 
        gaps defined by the variable `gaps`.
    tangent_model : {'TAN', 'MIF'}, optional
        Tangential force displacement relationship to use for asperities in 
        contact. 'TAN' corresponds to just the tangential stiffness then 
        complete slip at the friction coefficient. 'MIF' corresponds to the 
        Mindlin-Iwan Fit model, which approximates asperity microslip [1]_.
        The default is 'TAN'.
        
        
    Notes
    -----
    
    Implementation currently assumes that Nnl=3 (three nonlinear DOFs)
    The Nonlinear DOFs must first be both tangential displacement then 
    normal displacement
    
    References
    ----------
    .. [1] J. H. Porter and M. R. W. Brake, 2023, Towards a predictive, 
       physics-based friction model for the dynamics of jointed structures,
       Mechanical Systems and Signal Processing
  
    """

    def __init__(self, Q, T, ElasticMod, PoissonRatio, Radius, TangentMod, 
                 YieldStress, mu, u0=0, meso_gap=0, gaps=None, 
                 gap_weights=None, tangent_model='TAN'):
        
        self.Q = np.asarray(Q)
        self.T = np.asarray(T)
        
        if not type(self.Q) == type(Q):
            warnings.warn('Matrix Q argument is not a numpy array. Conversion '
                          'to numpy array was attempted, but not '
                          'guaranteed to work.')
            
        if not type(self.T) == type(T):
            warnings.warn('Matrix T argument is not a numpy array. Conversion '
                          'to numpy array was attempted, but not '
                          'guaranteed to work.')
        
        self.elastic_mod = ElasticMod
        self.poisson = PoissonRatio
        self.Re = Radius
        self.tangent_mod = TangentMod
        self.sys = YieldStress
        self.mu = mu # This friction coefficient sometimes switches between real and prestress
        self.real_mu = mu # Save real friction coefficient
        self.prestress_mu = 0.0 # Prestress should use zero friction coefficient
        
        self.tangent_model = tangent_model.upper()
        
        assert self.tangent_model in ['TAN', 'MIF'], \
            "Invalid option for tangent model. Options are: ['TAN', 'MIF']"
        
        self.u0 = u0
        
        # Calculated Initial Parameters
        self.Estar = ElasticMod / 2.0 / (1.0 - PoissonRatio**2)
        
        self.shear_mod = ElasticMod / 2.0 / (1.0 + PoissonRatio)

        self.Gstar = self.shear_mod / 2.0 / (2.0 - PoissonRatio)
        
        # Plasticity Parameters
        C = 1.295*np.exp(0.736*self.poisson);
        
        # displacement of one sphere against a rigid flat to cause yielding.
        delta_y1s = (np.pi*C*self.sys/(2*(2*self.Estar)))**2*(2*self.Re); 
        
        self.delta_y = delta_y1s*2
        
        # Topology and distribution of asperity parameters 
        self.meso_gap = meso_gap
        
        if gaps is None:
            # self.gaps = 
            # self.gap_weights = 
            assert False, "Need to implement case without specifying gap weights directly."
        else:
            self.gaps = gaps
            self.gap_weights = gap_weights
            
            assert gap_weights is not None, "Need to specify gap weights if specifying gap values."
            
        # Just consider default of starting sliders at origin for AFT
        self.uxyn_initialize = np.array([0.0, 0.0, 0.0])
            
        # Initialize History Variables
        self.init_history()

    def nl_force_type(self):
        """
        Marks as a hysteretic force.
        """
        return 1 
    
    def init_history(self):
        
        self.unmax = 0
        self.Fm_prev = np.zeros_like(self.gap_weights)
        self.fxy0 = np.zeros((self.gap_weights.shape[0], 2))
        self.uxyn0 = np.zeros(3)
    
    def update_history(self, uxyn, Fm_curr, fxy_curr):
        
        self.unmax = np.maximum(uxyn[-1], self.unmax)
        self.Fm_prev = Fm_curr
        self.fxy0 = fxy_curr
        self.uxyn0 = uxyn
        
    def set_prestress_mu(self):
        """
        Set friction coefficient to a different value (generally 0.0) for
        prestress analysis
        """
        self.mu = self.prestress_mu
        
    def reset_real_mu(self): 
        """
        Reset friction coefficient to a real value (generally not 0.0) for
        dynamic analysis
        """
        self.mu = self.real_mu
        
    def set_aft_initialize(self, X):
        """
        Set a center for frictional sliders to be initialized at zero force
        for AFT routines. 

        Parameters
        ----------
        X : np.array, size (Ndof,)
            Solution to a static solution or other desired set of displacements
            that will be used as the baseline position of frictional sliders.

        Returns
        -------
        None.

        """
        
        self.uxyn_initialize = self.Q @ X
        
        return
    
    def force(self, X, update_hist=False, return_aux=False):
        """
        Static Force Evaluation
        
        NOTE: Static Force evaluation does not support non-zero tangential 
        displacements. In short, the contact radius is discontinuous upon 
        normal load reversal and thus tangential stiffness is discontinuous
        This can potentially cause a large jump in tangential force based on 
        slight normal displacement variations and break solvers. 
        
        [Future Update - Allow for tangential displacements so can get 
         stiffness for linearized eigen solve on unloading curve.]

        Parameters
        ----------
        X : TYPE
            DESCRIPTION.
        update_hist : TYPE, optional
            DESCRIPTION. The default is False.
        return_aux : flag to return extra results about the simulation (aux)

        Returns
        -------
        F : TYPE
            DESCRIPTION.
        dFdX : TYPE
            DESCRIPTION.
        aux : Tuple of extra results includes (Fm_prev, deltabar, Rebar, a)
                Fm_prev : previous maximum normal force per asperity
                deltabar : permanent deformation displacement of each asperity
                Rebar : flattened (new) radius of each asperity
                a : radius of contact area of each asperity.

        """
        uxyn = self.Q @ X
        
        # Local Force evaluation based on unl
        dfnldunl, fnl, aux = _static_force_grad(uxyn, self.uxyn0, self.fxy0, 
                                            self.unmax, self.Fm_prev, 
                                            self.mu, self.meso_gap, self.gaps, 
                                            self.gap_weights, self.Re, 
                                            self.poisson, self.Estar, 
                                            self.elastic_mod, 
                                            self.tangent_mod, self.delta_y, 
                                            self.sys, self.Gstar)
        
        Fm_curr = aux[1]
        fxy_curr = aux[2]
        
        # Convert Back to Physical
        F = self.T @ fnl
        
        dFdX = self.T @ dfnldunl @ self.Q
        
        if update_hist:
            self.update_history(uxyn, Fm_curr, fxy_curr)
            
        if return_aux:
            return F, dFdX, aux[1:]
            
        else:
            return F, dFdX
    
    
    def local_force_history(self, unlt, unltdot, h, cst, unlth0, max_repeats=2, \
                            atol=1e-10, rtol=1e-10):
        """
        Calculates local force history for a single element of the rough 
        contact model given the local nonlinear displacements over a cycle.

        Parameters
        ----------
        unlt : (Nt, 3) numpy.ndarray
            Displacement history for a single cycle.
        unltdot : (Nt, 3) numpy.ndarray
            Velocity history for the cycle (ignored)
        h : list of harmonics used (ignored)
        cst : (ignored)
        unlth0 : Initialization displacements for the sliders 
        max_repeats : Number of times to repeat the cycle of displacements
                      to converge the hysteresis loops to steady-state
        atol : Ignored
        rtol : Ignored

        Returns
        -------
        fxyn_t : Tuple of (Nt, 3) numpy.ndarray
            Force history for the element. Returned as tuple

        """
        
        fxyn_t = _local_force_history(unlt, unlth0, 
                                    self.mu, self.meso_gap, self.gaps, 
                                    self.gap_weights, self.Re, self.poisson, 
                                    self.Estar, self.elastic_mod, self.tangent_mod, 
                                    self.delta_y, self.sys, self.Gstar, 
                                    repeats=max_repeats, 
                                    tangent_model=self.tangent_model)
        
        # typical return statement also requires derivatives, but this is just
        # for external processing and AFT will use the private function
        # return ft, dfduh, dfdudh
        
        return (fxyn_t,)
        

    def aft(self, U, w, h, Nt=128, tol=1e-7, max_repeats=2, return_local=False,
            calc_grad=True):
        """
        
        Tolerances are ignored since slider representation should converge
        with two repeated cycles (done by default). 
        two cycles of the hysteresis loop, so that is done by default. 

        Parameters
        ----------
        U : np.array, size (Nhc*nd,)
            Global DOFs harmonic coefficients, all 0th, then 1st cos, etc, 
        w : double (scalar)
            Frequency
        h : np.array, sorted
            List of harmonics that are considered, zeroth must be first
        Nt : Integer power of 2
             Number of time points to evaluate at. 
             The default is 128.
        tol : scalar
            Optional argument ignored, kept to match compatability with general
            AFT. 
        max_repeats : integer
                      number of hysteresis loops to calculate to reach steady
                      state. Maximum value allowed. 
                      The default is 2
        return_local : Boolean
                        If False, it uses self.Q and self.T to convert forces 
                        and gradients back to global domain.  If True, it does
                        not apply these transforms to the results.
                        default is False
        calc_grad : boolean
            Flag where True indicates that the gradients should be calculated 
            and returned. If False, then returns only (Fnl,) as a tuple. 
            The default is True

        Returns
        -------
        Fnl : np.array, size (Nhc*nd,)
            Vector of nonlinear forces in frequency domain
        dFnldU : np.array, size (Nhc*nd,Nhc*nd)
            Gradient of nonlinear forces w.r.t. U
        dFnldw : np.array, size (Nhc*nd,)
            Gradient w.r.t. w

        """
        
        #########################
        # Memory Initialization 
        
        Fnl = np.zeros_like(U)
        if calc_grad:
            dFnldU = np.zeros((U.shape[0], U.shape[0]))
            dFnldw = np.zeros_like(U)
        
        
        #########################
        # Transform to Local Coordinates
        
        Nhc = 2*(h !=0).sum() + (h==0).sum() # Number of Harmonic Components        
        Ulocal = (self.Q @ np.reshape(U, (self.Q.shape[1], Nhc), 'F')).T
        
        # Number of Nonlinear DOFs
        Ndnl = self.Q.shape[0]
        
        
        #########################
        # Conduct AFT in Local Coordinates with JAX
        Uwlocal = np.hstack((np.reshape(Ulocal.T, (Ndnl*Nhc,), 'F'), w))
        
        if calc_grad:
            # Case with gradient and local force
            dFdUwlocal, Flocal = _local_aft_grad(Uwlocal, self.uxyn_initialize, 
                                    self.mu, self.meso_gap, self.gaps, 
                                    self.gap_weights, self.Re, self.poisson, 
                                    self.Estar, self.elastic_mod, self.tangent_mod, 
                                    self.delta_y, self.sys, self.Gstar, 
                                    tuple(h), Nt, repeats=max_repeats,
                                    tangent_model=self.tangent_model)
        else:
            Flocal,_ = _local_aft(Uwlocal, self.uxyn_initialize, 
                                    self.mu, self.meso_gap, self.gaps, 
                                    self.gap_weights, self.Re, self.poisson, 
                                    self.Estar, self.elastic_mod, self.tangent_mod, 
                                    self.delta_y, self.sys, self.Gstar, 
                                    tuple(h), Nt, repeats=max_repeats,
                                    tangent_model=self.tangent_model)
        
        #########################
        # Option to return local results
        
        # Reshape Flocal
        Flocal = jnp.reshape(Flocal, (Ndnl, Nhc), 'F')
        
        if return_local:
            
            if calc_grad:
                return Flocal, dFdUwlocal[:, :-1], dFdUwlocal[:, -1]
            else:
                return (Flocal,)
        
        #########################
        # Convert AFT to Global Coordinates
                
        # Global coordinates        
        Fnl = np.reshape(self.T @ Flocal, (U.shape[0],), 'F')
        
        if calc_grad:
            dFnldU = np.kron(np.eye(Nhc), self.T) @ dFdUwlocal[:, :-1] \
                                                @ np.kron(np.eye(Nhc), self.Q)
        
            dFnldw = np.reshape(self.T @ \
                            np.reshape(dFdUwlocal[:, -1], (Ndnl, Nhc)), \
                            (U.shape[0],), 'F')
            
            return Fnl, dFnldU, dFnldw
        else:
            return (Fnl,)
        
        

    
###############################################################################
########## Static Force Functions                                    ##########
###############################################################################
    
# @partial(jax.jit, static_argnums=(7, 8, 9, 10, 11, 12, 13)) 
def _static_force(uxyn, uxyn0, fxy0, unmax, Fm_prev, mu, meso_gap, gaps, gap_weights,
                  Re, Possion, Estar, Emod, Etan, delta_y, Sys, Gstar):
    """
    Calculates the static Rough Contact Force between two surfaces at a single
    quadrature point of the FEM model.
    
    Recommended: mu = 0 for prestress analysis 
        and mu > 0 for linearized eigen analysis after prestress
        
    The rough contact model uses Nasp asperities that can be in contact
    
    Returns traction on element if gap_weights includes the asperity density. 

    Parameters
    ----------
    uxyn : Displacements in x,y,n directions at point. normal is positive into 
            surface
    uxyn : Displacements in x,y,n directions at previous instant
    fxy0 : Previous tangential forces for each asperity (Nasp, 2)
    unmax : Maximum previous displacement in normal direction at point (not 
                                                        including this step)
    Fm_prev : Previous maximum asperity contact forces for each asperity 
              size: (Nasp,)
    mu : Scalar friction coefficient
    meso_gap : Mesoscale topology gap of the element used to adjust 
                uxyn and unmax, scalar
    gaps : List of initial asperity gaps (w/o mesoscale topology), size (Nasp,)
    gap_weights : weights for integration over asperity gaps, size (Nasp,) equal to:
                 (quadrature weights)*(probability distribution)*(asperity density)
    Re : Effective radius of asperities in contact (half of real radius, see paper)
    Possion : Poisson's ratio of asperities
    Estar : Effective elastic modulus of contact
    Emod : Linear elastic modulus
    Etan : Hardening modulus for plasticity regime
    delta_y : Displacement of asperities that causes yielding to occur
    Sys : Yield strength of asperities
    Gstar : Combined shear modulus used for Mindlin tangential contact stiffness

    Returns
    -------
    fxyn : Contact forces or tractions in x,y,n directions. Exact definition
            depends on choice of gap_weights for the integration
    aux : containts fields of: (fxyn, Fm_prev, deltabar, Rebar)
            fxyn - same as above, duplicated for autodiff output
            Fm_prev - updated maximum normal forces for each asperity
            deltabar - permanent deformation of each asperity
            Rebar - updated radius due to permanent deformation of each asperity

    """
    
    # Recover deltam for each asperity based on previous maximum displacement
    deltam = unmax - meso_gap - gaps
    
    # Calculate normal displacement of each aspertiy
    un = uxyn[-1] - meso_gap - gaps

    fn_curr, a, deltabar, Rebar = asp_funs._normal_asperity_general(un, deltam, Fm_prev, 
                                 Re, Possion, Estar, Emod, Etan, delta_y, Sys)
    
    fxy_curr = asp_funs._tangential_asperity(uxyn[:2], uxyn0[:2], fxy0, 
                                             fn_curr, a, Gstar, mu)
    
    # Update normal history variables
    Fm_prev = jnp.maximum(fn_curr, Fm_prev)
    
    fxyn = jnp.zeros(3)
    fxyn = fxyn.at[-1].set(fn_curr @ gap_weights)
    fxyn = fxyn.at[:2].set(gap_weights @ fxy_curr)
    
    # Extra outputs
    #   includes force so have the undifferentiated force when calling jax.jacfwd
    aux = (fxyn, Fm_prev, fxy_curr, deltabar, Rebar, a)
    
    return fxyn, aux

@partial(jax.jit, static_argnums=tuple(range(9, 17))) 
def _static_force_grad(uxyn, uxyn0, fxy0, unmax, Fm_prev, mu, meso_gap, gaps, gap_weights,
                       Re, Possion, Estar, Emod, Etan, delta_y, Sys, Gstar):
    """
    Returns Jacobian, Force, and Aux Data from "_static_force"
    
    See "_static_force" for documentation of inputs/outputs

    """
    
    jax_diff_fun = jax.jacfwd(_static_force, has_aux=True) 
    
    J, aux = jax_diff_fun(uxyn, uxyn0, fxy0, unmax, Fm_prev, mu, meso_gap, gaps, gap_weights,
                           Re, Possion, Estar, Emod, Etan, delta_y, Sys, Gstar)
    
    F = aux[0]
    
    return J, F, aux


    
###############################################################################
########## Harmonic Force History Functions                          ##########
###############################################################################


@partial(jax.jit, static_argnums=(10,)) 
def _local_loop_body(ind, history, unlt, mu, meso_gap, gaps, gap_weights,
                       Re, Estar, Gstar, tangent_model='TAN', wquad=0):
    """
    Calculation of total rough contact forces for a given instant in a time 
    series. Formatted to allow for calling jax.lax.fori_loop
    
    In general, considering the loop for Nt time points. 
    Considers Nasp=gaps.shape[0] asperities in contact for the element.

    Parameters
    ----------
    ind : Time index to be calculated for this iteration
    history : Tuple of variables to use for history includes in order: 
                fxyn_t : contact forces/tractions totalled over all asperities 
                        for all time instants (Nt, 3)
                uxyn0 : previous set of displacements (in general unlt[ind-1, :])
                            required so that initialization can be user defined
                fxy0 : tangential force for each asperity at the previous 
                        instant shape (Nasp, 2) for 'TAN'.
                        For 'MIF' (Nasp, Nradius, 2)
                deltam : Maximum normal displacement of each asperity for any 
                        time (Nasp,)
                Fm : Forces in the normal direction for instant of maximum 
                        displacement
                rquad0 : (Nasp, Nradius) numpy.ndarray
                    Ignored for 'TAN' model, so can be anything. 
                    Quadrature radii including maximum radius for 'MIF' 
                    model. Rows are asperities, columns are scaled radii
                    for each asperity. 
    unlt : Time series of displacements for nonlinear force evaluations
            Size (Nt, 3)
    mu, meso_gap, gaps, gap_weights, Re, Estar, Gstar : 
            See RoughContactFriction Class Documentation
    tangent_model : {'TAN', 'MIF'}, optional
        Flag for tangent asperity model (stick-slip) or Mindlin-Iwan Fit model
        (microslip at each asperity).
        This must be static for JAX.
        The default is 'TAN'.
    wquad : (Nradius,) numpy.ndarray, optional
        Quadrature integration weights for MIF model for radial discretization
        of asperity contact areas.
        The default is 0, but should only be used for 'TAN' model.

    Returns
    -------
    history : Same as input, but updated for the instant ind

    """
    
    fxyn_t, uxyn0, fxy0, deltam, Fm, rquad0 = history
    
    # Asperity force calculation
    
    # Normal Direction forces, exclusively on the plastic unloading curve
    # Elastic Unloading after Plasticity
    un = unlt[ind, 2] - meso_gap - gaps
    
    fn_curr, a, deltabar, Rebar = asp_funs._normal_asperity_unloading(un, 
                                                        deltam, Fm, Re, Estar)
    
    # Tangential Forces
    if tangent_model == 'TAN':
        fxy_curr = asp_funs._tangential_asperity(unlt[ind, :2], uxyn0[:2], fxy0, 
                                             fn_curr, a, Gstar, mu)
        
        # quadrature radii for contact are ignored with TAN model.
        rquad_curr = rquad0
    elif tangent_model == 'MIF':
        fxy_curr, rquad_curr = asp_funs._tangential_asperity_mif(unlt[ind, :2], 
                                        uxyn0[:2], fxy0, fn_curr, a, Gstar, mu, 
                                        rquad0, wquad)
        
    
    # Integrate asperity forces into total element in contact forces
    fxyn_t = fxyn_t.at[ind, :2].set(gap_weights @ fxy_curr)
    fxyn_t = fxyn_t.at[ind, -1].set(fn_curr @ gap_weights)
    
    history = (fxyn_t, unlt[ind, :], fxy_curr, deltam, Fm, rquad_curr)
    
    return history
    

@partial(jax.jit, static_argnums=tuple(range(6, 16))) 
def _local_force_history(unlt, unlth0, mu, meso_gap, gaps, gap_weights,
                         Re, Possion, Estar, Emod, Etan, delta_y, Sys, Gstar, 
                         repeats=2, tangent_model='TAN'):
    """
    Calculates the steady-state displacement history for a set of displacements
    
    NOTES:
        This function throws an error if not JIT compiled because the loop body
        would be dependent on a non-static argument in that case.

    Parameters
    ----------
    unlt : Time history of displacements to evaluate cycles over - size (Nt, 3)
    unlth0 : Displacements to initialize the first loop iteration to. 
             For asperities that do not slip, this influences the static force.
    mu, meso_gap, gaps, gap_weights, Re, Possion, Estar, Emod, Etan, delta_y, 
    Sys, Gstar : 
        See RoughContactFriction Class Documentation
    repeats : The number of repeated cycles the nonlinear forces should be 
                evaluated for to obtain steady-state forces. Default is 2

    Returns
    -------
    fxyn_t : History of total forces (Nt, 3)

    """
    
    Nt = unlt.shape[0]
    
    ###########
    # Initialize the Normal Direction Force Calculation
    unmax = unlt[:, -1].max()
    
    Fm_prev = jnp.zeros_like(gap_weights)
    
    # Recover deltam for each asperity based on previous maximum displacement
    deltam = 0 - meso_gap - gaps
    
    # Calculate normal displacement of each aspertiy
    unmax_asp = unmax - meso_gap - gaps
    
    fn, a, deltabar, Rebar = asp_funs._normal_asperity_general(unmax_asp, deltam, Fm_prev, 
                                 Re, Possion, Estar, Emod, Etan, delta_y, Sys)
    
    ###########
    # Generate a history tuple for use in the function
    fxy0 = np.zeros((gap_weights.shape[0], 2)) # previous instant of asperity forces
    uxyn0 = unlth0*1.0 # Previous instant of displacements (force to be double)
    fxyn_t = jnp.zeros((Nt, 3)) # History of total contact forces (summed over asperities)
    # deltam = unmax_asp # Maximum normal displacement at each element
    # fm = fn # Normal asperity forces for maximum normal displacement
    
    if tangent_model == 'TAN':
        rquad = 0 # ignored in history with TAN model
        wquad = 0 # ignored for TAN model
    elif tangent_model == 'MIF':
        assert False, 'Need to initialize rquad, wquad here.'
        
    history = (fxyn_t, uxyn0, fxy0, unmax_asp, fn, rquad)
    
    ###########
    # Loop body function
    
    loop_fun = lambda i,hist : _local_loop_body(i, hist, unlt, mu, meso_gap, 
                                            gaps, gap_weights, Re, Estar, Gstar,
                                            tangent_model=tangent_model,
                                            wquad=wquad)
    
    # import pdb; pdb.set_trace()
    
    ###########
    # Do a loop over the set of Nt samples, repeating to get convergence 
    # to steady-state forces
    for i in range(repeats):
        history = jax.lax.fori_loop(0, Nt, loop_fun, history)
    
    ###########
    # Extract the steady-state forces
    fxyn_t = history[0]

    return fxyn_t


###############################################################################
########## AFT Functions                                             ##########
###############################################################################


@partial(jax.jit, static_argnums=tuple(range(6, 18))) 
def _local_aft(Uwlocal, unlth0, mu, meso_gap, gaps, gap_weights,
                         Re, Possion, Estar, Emod, Etan, delta_y, Sys, Gstar, 
                         htuple, Nt, repeats=2, tangent_model='TAN'):
    ########################################
    #### Initialization
    
    # Size Calculation
    Nhc = hutils.Nhc(np.array(htuple))
    Ndnl = int(Uwlocal.shape[0] / Nhc)
    
    # Uwlocal is arranged as all of harmonic 0, then all of 1c, etc. 
    # For each harmonic it has the DOFs in order. Finally there is frequency.
    # This is a 1d array. 
    #
    # Ulocal is (Nhc x Ndnl) - each column is the harmonic components for a 
    # single nonlinear DOF.
    Ulocal = jnp.reshape(Uwlocal[:-1], (Ndnl, Nhc), 'F').T

    ########################################
    #### Displacements
    
    # Nonlinear displacements, velocities in time
    # Nt x Ndnl
    unlt = jhutils.time_series_deriv(Nt, htuple, Ulocal, 0) # Nt x Ndnl
    
    
    ########################################
    #### Evaluate nonlinear forces
    
    ft = _local_force_history(unlt, unlth0, mu, meso_gap, gaps, gap_weights,
                             Re, Possion, Estar, Emod, Etan, delta_y, Sys, Gstar, 
                             repeats, tangent_model=tangent_model)
    
    # Convert back into frequency domain
    Flocal = jhutils.get_fourier_coeff(htuple, ft)
    
    # Flatten back to a 1D array
    Flocal = jnp.reshape(Flocal.T, (-1,), 'F')
    
    return Flocal,Flocal


@partial(jax.jit, static_argnums=tuple(range(6, 18))) 
def _local_aft_grad(Uwlocal, unlth0, mu, meso_gap, gaps, gap_weights,
                         Re, Possion, Estar, Emod, Etan, delta_y, Sys, Gstar, 
                         htuple, Nt, repeats=2, tangent_model='TAN'):
    """
    Gradient of _local_aft - see _local_aft for documentation. 

    """
    
    J,F = jax.jacfwd(_local_aft, has_aux=True)(Uwlocal, unlth0, mu, meso_gap, 
                                               gaps, gap_weights, Re, Possion, 
                                               Estar, Emod, Etan, delta_y, Sys, 
                                               Gstar, htuple, Nt, repeats, 
                                               tangent_model=tangent_model)    
    return J,F



