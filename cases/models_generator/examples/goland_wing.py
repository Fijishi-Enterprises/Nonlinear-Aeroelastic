import numpy as np
import os
import sys
import importlib
import cases.models_generator.gen_main as gm
import sharpy.utils.algebra as algebra
importlib.reload(gm)

#############################
# Read goland wing from docs #
#############################
split_path = [sys.path[i].split('/') for i in [0,1,2]]
for i in range(len(split_path)):
    if 'sharpy' in split_path[i]:
        ind = i
        break
    
sharpy_dir = sys.path[ind]
path2goland = sharpy_dir + '/docs/source/content/example_notebooks/cases'
goland_files = os.listdir(path2goland)
for i in goland_files:
    if 'goland' in i and 'sharpy' in i:
        goland_sharpy = i
    elif 'goland' in i and 'fem' in i:
        goland_fem = i
    elif 'goland' in i and 'aero' in i:
        goland_aero = i
        
goland = gm.Sharpy_data(['read_structure','read_aero','simulation'])
goland.read_structure(path2goland+'/'+goland_fem)
goland.read_aero(path2goland+'/'+goland_aero)
goland.read_sim(path2goland+'/'+goland_sharpy)

goland2 = gm.Sharpy_data(['read_structure','read_aero','simulation'])
goland2.read_structure('/mnt/work/Programs/sharpy/runs_goland/golandz0_0_0/golandz.fem.h5')
goland2.read_aero('/mnt/work/Programs/sharpy/runs_goland/golandz0_0_0/golandz.aero.h5')
goland2.read_sim('/mnt/work/Programs/sharpy/runs_goland/golandz0_0_0/golandz.sharpy')

# aeroelasticity parameters
main_ea = 0.33
main_cg = 0.43
sigma = 1
c_ref = 1.8288
ea, ga = 1e9, 1e9
gj = 0.987581e6
eiy = 9.77221e6
eiz = 1e2 * eiy
base_stiffness = np.diag([ea, ga, ga, sigma * gj, sigma * eiy, eiz])
stiffness = np.zeros((1, 6, 6))
stiffness[0] = base_stiffness
m_unit = 35.71
j_tors = 8.64
pos_cg_b = np.array([0., c_ref * (main_cg - main_ea), 0.])
m_chi_cg = algebra.skew(m_unit * pos_cg_b)
mass = np.zeros((1, 6, 6))
mass[0, :, :] = np.diag([m_unit, m_unit, m_unit,
                         j_tors, .1 * j_tors, .9 * j_tors])
mass[0, :3, 3:] = m_chi_cg
mass[0, 3:, :3] = -m_chi_cg
# goland.fem['stiffness_db'] = stiffness
# goland.fem['mass_db'] = mass
# goland.aero['elastic_axis']
# .
# .
# .

g1c = dict()
g1c['workflow'] = ['create_structure','create_aero']
g1c['geometry'] = {'length':6.096,
                   'num_node':17,
                   'direction':None,
                   'node0':[0., 0., 0.],
                   'sweep':0.,
                   'dihedral':0.}
g1c['fem'] = {'stiffness_db':stiffness,
              'mass_db':mass,
              'frame_of_reference_delta':[-1.,0.,0.]}
g1c['aero'] = {'chord':c_ref,
              'elastic_axis':main_ea,
              'surface_m':16}

comp1 = gm.Components('wing1','sharpy',['sharpy'],g1c)
g1cm = {'wing1':g1c}
g1mm = {'model_name':'goland',
        'model_route':sharpy_dir+'/cases/models_generator/examples/goland_wing',
        'iterate_vars': {'wing1*geometry-sweep':np.linspace(0.,30*np.pi/180,3),
                         'wing1*geometry-length':6.096*np.linspace(0.8,2.,3),
                         'wing1*fem-sigma':[0.8,1.,1.2,]},
        'iterate_labels': {'label_type':'number',
                           'print_name_var':0}}

g1sm = {'sharpy': {'simulation_input':None,
                   'default_module':'sharpy.routines.flutter',
                   'default_solution':'sol_145',
                   'default_solution_vars': {'panels_wake':16*5,
                                             'num_modes':5,
                                             'rho':1.02,
                                             'u_inf':0.01,
                                             'c_ref':1.8288,
                                             'folder':'./runs',
                                             'velocity_analysis':[70,220,150],
                                             'rom_algorithm':'mimo_rational_arnoldi',
                                             'rom_size':6,
                                             'flow':['BeamLoader',
                                                     'AerogridLoader',
                                                     'StaticCoupled',
                                                     'AerogridPlot',
                                                     'Modal',
                                                     'LinearAssembler',
                                                     'AsymptoticStability'],
                                             'AerogridPlot':{'folder':'./runs',
                                                             'include_rbm': 'off',
                                                             'include_applied_forces': 'off',
                                                             'minus_m_star': 0}},
                   'default_sharpy':{},
                   'model_route':None}}

g1 = gm.Model('sharpy',['sharpy'], model_dict=g1mm, components_dict=g1cm,
              simulation_dict=g1sm)
g1.run()