"""
Example Script for a Simulation of the Brake-Reuss Beam utilizing an advanced
rough contact model. This recreates one of the simulations from [1]. For full
simulation descriptions and many details about the model and methods see [1].

Friction Model: Rough Contact [1] (TODO: Add flag for Elastic Dry Friction)

Nonlinear Modal Analysis: Extended Periodic Motion Concept

Model: 232 Zero Thickness Elements (ZTEs) [Hyper Reduction Paper]
        Model file: matrices/ROM_U_232ELS4py.mat
        Model file must be downloaded from storage elsewhere. See README.md
        
Surface Parameters: Surface parameters for rough contact are identified in [1]
        Surface Parameters file: matrices/combined_14sep21_R1_4py.mat
        Surface parameter file must be downloaded from storage elsewhere. See
        README.md

Reference Papers:
 [1] Porter, Justin H., and Matthew R. W. Brake. "Towards a Predictive, 
     Physics-Based Friction Model for the Dynamics of Jointed Structures." 
     Mechanical Systems and Signal Processing 192 (June 1, 2023): 110210.
     https://doi.org/10.1016/j.ymssp.2023.110210.


TODO : 
    1. Readme for file downloads for matrices etc.
    2. Terminology/nomenclature in this comment?
    3. Add elastic dry friction flag option?

"""

import sys
import numpy as np
from scipy import io as sio
import warnings

sys.path.append('../..')
from tmdsimpy import harmonic_utils as hutils

from tmdsimpy.solvers import NonlinearSolver
from tmdsimpy.solvers_omp import NonlinearSolverOMP

from tmdsimpy.continuation import Continuation

# from tmdsimpy.vibration_system import VibrationSystem
from tmdsimpy.vibration_system_sm import VibrationSystemSM as VibrationSystem
from tmdsimpy.jax.nlforces.roughcontact.rough_contact import RoughContactFriction


# Everything has to be split off into main to work with parallelization
if __name__ == '__main__':
    
    ###############################################################################
    ####### User Inputs                                                     #######
    ###############################################################################
    
    # File name for a .mat file that contains the matrices appropriately formatted
    # to be able to describe the structural system. 
    system_fname = './matrices/ROM_U_232ELS4py.mat'
    
    
    # Estimated displacements. Because the rough contact stiffness goes to zero
    # when contact is at zero displacement, an initial estimate of the normal 
    # displacement is needed to get an initial stiffness to generate an initial
    # guess for the nonlinear solver. 
    uxyn_est = np.array([0, 0, .1e-5])
    
    # Surface Parameters for the rough contact model
    surface_fname = './matrices/combined_14sep21_R1_4py.mat'
    
    # Log Amplitude Start
    Astart = -7.7
    
    # Log Amplitude End
    Aend = -4.2
    
    # Continuation Step Size (starting)
    ds = 0.1
    dsmax = 0.2
    dsmin = 0.02
    
    
    fast_sol = False # Choose speed or accuracy
    
    if fast_sol:
        # Run with reduced harmonics and AFT time steps to keep time within a 
        # few minutes
        h_max = 1
        Nt = 1<<3
    else:
        # Normal - settings for higher accuracy as used in previous papers
        h_max = 3
        Nt = 1<<7
        
    run_profilers = False # Run profiling of code operations to identify bottlenecks
    
    # solve function - can use python library routines or custom ones
    # solver = NonlinearSolver() # scipy nonlinear solver
    solver = NonlinearSolverOMP() # Custom Newton-Raphson solver
    
    ###############################################################################
    ####### Load System Matrices from .mat File                             #######
    ###############################################################################
    
    system_matrices = sio.loadmat(system_fname)
    
    ######## Sanity Checks on Loaded Matrices
    
    # Sizes
    assert system_matrices['M'].shape == system_matrices['K'].shape, \
            'Mass and stiffness matrices are not the same size, this will not work.'
    
    if not (system_matrices['M'].shape == (859, 859)):
        warnings.warn("Warning: Mass and stiffness matrices are not the expected "\
                      "size for the UROM 232 Model.")
    
    # Approximate Frequencies Without Contact
    # If running a different ROM, these will vary slightly
    solver = NonlinearSolverOMP()
    
    eigvals, eigvecs = solver.eigs(system_matrices['K'], system_matrices['M'], 
                                   subset_by_index=[0, 9])
    
    expected_eigvals = np.array([1.855211e+01, 1.701181e+05, 8.196151e+05, 
                                  1.368695e+07, 1.543605e+07, 1.871511e+07, 
                                  1.975941e+07, 2.692282e+07, 3.442458e+07, 
                                  8.631324e+07])
    
    first_eig_ratio = eigvals[0] / eigvals[1]
    eig_diff = np.abs((eigvals - expected_eigvals)/expected_eigvals)[1:].max()
    
    if first_eig_ratio > 1e-3:
        warnings.warn("Expected rigid body mode of first eigenvalue "
              "is high: ratio eigvals[0]/eigvals[1]={:.3e}".format(first_eig_ratio))
    
    if eig_diff > 1e-4:
        warnings.warn("Eigenvalues differed by fraction: {:.3e}".format(eig_diff))
    
    # Check that the integrated area is as expected
    ref_area = 0.002921034742767
    area_error = (system_matrices['Tm'].sum() - ref_area) / ref_area
    
    assert area_error < 1e-4, 'Quadrature integration matrix gives wrong contact area.'
    
    ###############################################################################
    ####### Friction Model Parameters                                       #######
    ###############################################################################
    
    surface_pars = sio.loadmat(surface_fname)
    
    ElasticMod = 192.31e9 # Pa
    PoissonRatio = 0.3
    Radius = surface_pars['Re'][0, 0] # m
    TangentMod = 620e6 # Pa
    YieldStress = 331.7e6 # Pa 
    mu = 0.03
    
    area_density = surface_pars['area_density'][0,0] # Asperities / m^2
    max_gap = surface_pars['z_max'][0,0] # m
    
    normzinterp = surface_pars['normzinterp'][0]
    pzinterp    = surface_pars['pzinterp'][0]
    
    gaps = np.linspace(0, 1.0, 101) * max_gap
    
    trap_weights = np.ones_like(gaps)
    trap_weights[1:-1] = 2.0
    trap_weights = trap_weights / trap_weights.sum()
    
    gap_weights = area_density * trap_weights * np.interp(gaps/max_gap, 
                                                          normzinterp, pzinterp)
    
    prestress = (12002+12075+12670)*1.0/3; # N per bolt
    
    ###############################################################################
    ####### Create Vibration System                                         #######
    ###############################################################################
    # Initial Guess Constructed based only on mass prop damping.
    damp_ab = [0.01, 0.0]
    
    vib_sys = VibrationSystem(system_matrices['M'], system_matrices['K'], 
                              ab=damp_ab)
    
    ###############################################################################
    ####### Add Nonlinear Forces to System                                  #######
    ###############################################################################
    
    # Number of nonlinear frictional elements, Number of Nodes
    Nnl,Nnodes = system_matrices['Qm'].shape 
    
    # Need to convert sparse loads into arrays so that operations are expected shapes
    # Sparse matrices from matlab are loaded as matrices rather than numpy arrays
    # and behave differently than numpy arrays.
    Qm = np.array(system_matrices['Qm'].todense()) 
    Tm = np.array(system_matrices['Tm'].todense())
    
    # Pull out for reference convenience
    L  = system_matrices['L']
    
    QL = np.kron(Qm, np.eye(3)) @ L[:3*Nnodes, :]
    LTT = L[:3*Nnodes, :].T @ np.kron(Tm, np.eye(3))
    
    for i in range(Nnl):
        
        Ls = (QL[i*3:(i*3+3), :])
        Lf = (LTT[:, i*3:(i*3+3)])
    
        tmp_nl_force = RoughContactFriction(Ls, Lf, ElasticMod, PoissonRatio, 
                                            Radius, TangentMod, YieldStress, mu,
                                            gaps=gaps, gap_weights=gap_weights)
        
        vib_sys.add_nl_force(tmp_nl_force)
        
        
    # Create a reference nonlinear element that can be used for initial guesses
    ref_nlforce = RoughContactFriction(np.eye(3), np.eye(3), ElasticMod, 
                                       PoissonRatio, Radius, TangentMod, 
                                       YieldStress, mu,
                                       gaps=gaps, gap_weights=gap_weights)
    
    ###############################################################################
    ####### Prestress Analysis                                              #######
    ###############################################################################
    
    vib_sys.set_prestress_mu()
    
    Fv = system_matrices['Fv'][:, 0]
    
    # Get an estimate of the stiffness at a contact
    t, dtduxyn = ref_nlforce.force(uxyn_est)
    
    # linearized stiffness matrix with normal contact friction 
    # Tangent friction is set to zero for prestress so do the same here.
    Kstuck = np.zeros((L.shape[0], L.shape[0]))
    
    place_normal = np.eye(3)
    place_normal[0,0] = 0
    place_normal[1,1] = 0
    kn_mat = Tm @ (dtduxyn[2,2] * Qm)
    Kstuck[:3*Nnodes, :3*Nnodes] += np.kron(kn_mat, place_normal)
    
    K0 = system_matrices['K'] + L.T @ Kstuck @ L
    
    # Calculate an initial guess
    X0 = np.linalg.solve(K0,(Fv * prestress))
    
    # function to solve
    pre_fun = lambda U : vib_sys.static_res(U, Fv*prestress)
    
    R0, dR0dX = pre_fun(X0)
    
    print('Residual norm of initial guess: {:.4e}'.format(np.linalg.norm(dR0dX)))
    
    import time
    
    t0 = time.time()
    Xpre, R, dRdX, sol = solver.nsolve(pre_fun, X0, verbose=True, xtol=1e-13)
    
    t1 = time.time()
    
    print('Residual norm: {:.4e}'.format(np.linalg.norm(R)))
    
    print('Static Solution Run Time : {:.3e} s'.format(t1 - t0))
    
    # Update history variables after static so sliders reset
    vib_sys.update_force_history(Xpre)
    
    # Use the prestress solution as the intial slider positions for AFT as well
    vib_sys.set_aft_initialize(Xpre)
    
    # Reset to real friction coefficient after updating frictionless slider
    # positions
    vib_sys.reset_real_mu()
    
    ###############################################################################
    ####### Updated Eigenvalue Analysis After Prestress                     #######
    ###############################################################################
    
    # Recalculate stiffness with real mu
    Rpre, dRpredX = vib_sys.static_res(Xpre, Fv*prestress)
    
    eigvals, eigvecs = solver.eigs(dRpredX, system_matrices['M'], 
                                    subset_by_index=[0, 9], symmetric=False)
    
    
    print('Prestress State Frequencies: [Hz]')
    print(np.sqrt(eigvals)/(2*np.pi))
    
    # Mass normalize eigenvectors
    norm = np.diag(eigvecs.T @ system_matrices['M'] @ eigvecs)
    eigvecs = eigvecs / np.sqrt(norm)
    
    # Displacement at accel for eigenvectors
    resp_amp = system_matrices['R'][2, :] @ eigvecs
    print('Response amplitudes at tip accel: [m]')
    print(resp_amp)
    
    print('Expected frequencies from previous MATLAB / Paper (Flat Mesoscale):'\
          +' 168.5026, 580.4082, 1177.6498 Hz')
    
    ###############################################################################
    ####### Profile Nonlinear Solve                                         #######
    ###############################################################################
    
    if run_profilers:
        
        import cProfile
        cProfile.run('solver.nsolve(pre_fun, X0, verbose=True, xtol=1e-13)')
        
        print('This indicates most time is spent in the residual function and not the matrix solves.')
        print('i.e.: "vibration_system.py:116(static_res)"')
        
        print('Type "c" to continue execution')
        import pdb; pdb.set_trace()
    
    
    ###############################################################################
    ####### EPMC Initial Guess                                              #######
    ###############################################################################
    
    h = np.array(range(h_max+1))
    
    Nhc = hutils.Nhc(h)
    
    Ndof = vib_sys.M.shape[0]
    
    Fl = np.zeros(Nhc*Ndof)
    
    # Static Forces
    Fl[:Ndof] = prestress*Fv
    Fl[Ndof:2*Ndof] = system_matrices['R'][2, :] # No cosine component at accel
    
    Uwxa0 = np.zeros(Nhc*Ndof + 3)
    
    # Static Displacements
    Uwxa0[:Ndof] = Xpre
    
    # Mode Shape
    mode_ind = 0
    Uwxa0[2*Ndof:3*Ndof] = np.real(eigvecs[:, mode_ind])
    
    # Linear Frequency
    Uwxa0[-3] = np.sqrt(np.real(eigvals[mode_ind]))
    
    # Initial Damping
    zeta = damp_ab[0] / 2 / Uwxa0[-3] 
    Uwxa0[-2] = 2*Uwxa0[-3]*zeta
    
    # Amplitude
    Uwxa0[-1] = Astart
    
    ###############################################################################
    ####### EPMC Continuation                                               #######
    ###############################################################################
    
    epmc_fun = lambda Uwxa : vib_sys.epmc_res(Uwxa, Fl, h, Nt=Nt)
    
    continue_config = {'DynamicCtoP': True, 
                       'TargetNfev' : 6,
                       'MaxSteps'   : 20,
                       'dsmin'      : dsmin,
                       'dsmax'      : dsmax, # 0.015 for plotting
                       'verbose'    : 1,
                       'xtol'       : 1e-6*Uwxa0.shape[0], 
                       'corrector'  : 'Ortho', # Ortho, Pseudo
                       'nsolve_verbose' : True}
    
    CtoP = hutils.harmonic_wise_conditioning(Uwxa0, Ndof, h)
    CtoP[-1] = np.abs(Aend-Astart)
    
    
    cont_solver = Continuation(solver, ds0=ds, CtoP=CtoP, config=continue_config)
    
    ######################
    t0 = time.time()
    
    epmc_fun(Uwxa0)
    
    t1 = time.time()
    print('Single Residual Time: {: 8.3f} seconds'.format(t1-t0))
    print('Estimated: JAX compiled parallelism only: 15 sec')
    
    #####################
    
    t0 = time.time()
    
    Uwxa_full = cont_solver.continuation(epmc_fun, Uwxa0, Astart, Aend)
    
    t1 = time.time()
    
    print('Continuation solve time: {: 8.3f} seconds'.format(t1-t0))
    
    
    ###############################################################################
    ####### Profile EPMC Continuation                                       #######
    ###############################################################################
    
    if run_profilers:
            
        import cProfile
        cProfile.run('cont_solver.continuation(epmc_fun, Uwxa0, Astart, Aend)')
        
        print('This shows most of the time is in the AFT evaluations (probably poorly spread with JAX)')
        print('Secondary, np.linalg.solve takes 3.2 seconds compared to 18 on svd')
        
        
    ###############################################################################
    ####### Open Debug Terminal at End for User to Query Variables          #######
    ###############################################################################
    
    import pdb; pdb.set_trace()