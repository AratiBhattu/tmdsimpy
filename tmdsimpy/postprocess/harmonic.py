"""
Submodule for postprocessing  harmonic solutions.
"""

import numpy as np

from ..utils import harmonic as hutils

def local_harmonic_forces(vibration_system, U, w, h, Nt=128, aft_tol=1e-7):
    """
    Calculate local harmonic forces for each nonlinear force in the vibration 
    system.

    Parameters
    ----------
    vibration_system : tmdsimpy.VibrationSystem
        Vibration system with nonlinear forces. The system has `N` degrees of 
        freedom with `N = vibration_system.M.shape[0]`.
    U : numpy.ndarray (N*Nhc,) 
        Harmonic DOFs, displacements, np.hstack((U0, U1c, U1s...)) with 
        harmonics `h`.
    w : double
        Frequency (rad/s)
    h : 1D numpy.ndarray, sorted
        Sorted list of harmonics
    Nt : integer, power of 2, optional
        Number of Time Steps for AFT. 
        The default is 128.
    aft_tol : double, optional
        Tolerance for AFT that is passed as an argument.
        The default is 1e-7.

    Returns
    -------
    Uh_ut_Fh_ft : list of tuples of size 4
        The indices and length of the list correspond to the list of nonlinear
        forces in vibration_system.nonlinear_forces.
        Each local nonlinear force is based on `Nnl` local displacements and 
        gives `Nnlf` local nonlinear forces.
        At each index in the list, the tuple contains: the local harmonic 
        coefficients (`(Nhc, Nnl) numpy.ndarray`), 
        the local time series of displacements (`(Nt, Nnl) numpy.ndarray`), 
        local nonlinear forces harmonic coefficients 
        (`(Nhc, Nnlf) numpy.ndarray`), and
        local nonlinear force time series (`(Nt, Nnlf) numpy.ndarray`).
        
    Notes
    -----
    
    The number of harmonic components is
    `Nhc = tmdsimpy.utils.harmonic.Nhc(h)`
    
    For the output of a given nonlinear force `nlforce`, the dimensions are
    
    >>> Nnl = nlforce.Q.shape[0]
    >>> Nnlf = nlforce.T.shape[1]
    
    In the future, a `calc_grad` option may be added to speed up computations
    by not calculating the gradient.

    """
    
    # Dimensions / Initialization
    Nhc = hutils.Nhc(h)
    
    Uh_ut_Fh_ft = [None] * len(vibration_system.nonlinear_forces)
    
    # Repeat AFT to return local information
    for ind, nlforce in enumerate(vibration_system.nonlinear_forces):
        
        # Local harmonic forces
        Uh = (nlforce.Q @ np.reshape(U, (nlforce.Q.shape[1], Nhc), 'F')).T
        
        ut = hutils.time_series_deriv(Nt, h, Uh, 0) # Nt x Ndnl
        unltdot = w*hutils.time_series_deriv(Nt, h, Uh, 1) # Nt x Ndnl
        
        cst = hutils.time_series_deriv(Nt, h, np.eye(Nhc), 0)
        
        if nlforce.nl_force_type() == 0:
            # Instantaneous Nonlinearities
            
            ft = nlforce.local_force_history(ut, unltdot)[0]
        
        if nlforce.nl_force_type() == 1:
            # Hysteretic Nonlinearities
            
            if hasattr(nlforce, 'u0'):
                # Generally the rough contact nonlinearity should come here,
                # and other nonlinear friction type elements.    
                unlth0 = nlforce.u0
            
            else:
                # Basic hysteretic force initialization
                unlth0 = Uh[0, :]
            
            ft = nlforce.local_force_history(ut, unltdot, h, cst, unlth0, 
                                                       atol=aft_tol)[0]
        
        Fh = hutils.get_fourier_coeff(h, ft)
        
        Uh_ut_Fh_ft[ind] = (Uh, ut, Fh, ft)

    return Uh_ut_Fh_ft