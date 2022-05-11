import ctypes as ct
import numpy as np
import os

from sharpy.utils.solver_interface import solver, BaseSolver, solver_from_string
import sharpy.utils.settings as settings
import sharpy.utils.solver_interface as solver_interface

import sharpy.utils.cout_utils as cout
import sharpy.structure.utils.xbeamlib as xbeamlib
import sharpy.utils.multibody as mb
import sharpy.structure.utils.lagrangeconstraints as lagrangeconstraints
import sharpy.utils.exceptions as exc


_BaseStructural = solver_from_string('_BaseStructural')


@solver
class NonLinearDynamicMultibody(_BaseStructural):
    """
    Nonlinear dynamic multibody

    Nonlinear dynamic step solver for multibody structures.

    """
    solver_id = 'NonLinearDynamicMultibody'
    solver_classification = 'structural'

    settings_types = _BaseStructural.settings_types.copy()
    settings_default = _BaseStructural.settings_default.copy()
    settings_description = _BaseStructural.settings_description.copy()
    settings_options = dict()

    settings_types['time_integrator'] = 'str'
    settings_default['time_integrator'] = 'NewmarkBeta'
    settings_description['time_integrator'] = 'Method to perform time integration'
    settings_options['time_integrator'] = ['NewmarkBeta']

    settings_types['time_integrator_settings'] = 'dict'
    settings_default['time_integrator_settings'] = dict()
    settings_description['time_integrator_settings'] = 'Settings for the time integrator'

    settings_types['write_lm'] = 'bool'
    settings_default['write_lm'] = False
    settings_description['write_lm'] = 'Write lagrange multipliers'

    settings_types['relax_factor_lm'] = 'float'
    settings_default['relax_factor_lm'] = 0.
    settings_description['relax_factor_lm'] = 'Relaxation factor for Lagrange Multipliers. 0 no relaxation. 1 full relaxation'

    settings_table = settings.SettingsTable()
    __doc__ += settings_table.generate(settings_types, settings_default, settings_description)

    def __init__(self):
        self.data = None
        self.settings = None

        # Total number of unknowns in the Multybody sistem
        self.sys_size = None

        # Total number of equations associated to the Lagrange multipliers
        self.lc_list = None
        self.num_LM_eq = None
        self.Lambda = None
        self.Lambda_dot = None
        self.Lambda_ddot = None

        self.gamma = None
        self.beta = None

        self.prev_Dq = None

    def initialise(self, data, custom_settings=None):

        self.data = data
        if custom_settings is None:
            self.settings = data.settings[self.solver_id]
        else:
            self.settings = custom_settings
        settings.to_custom_types(self.settings, self.settings_types, self.settings_default, no_ctype=True)

        # load info from dyn dictionary
        self.data.structure.add_unsteady_information(
            self.data.structure.dyn_dict, self.settings['num_steps'])

        # Initialise time integrator
        self.time_integrator = solver_interface.initialise_solver(
            self.settings['time_integrator'])
        self.time_integrator.initialise(
            self.data, self.settings['time_integrator_settings'])

        # Define the number of equations
        self.lc_list = lagrangeconstraints.initialize_constraints(self.data.structure.ini_mb_dict)
        self.num_LM_eq = lagrangeconstraints.define_num_LM_eq(self.lc_list)

        self.Lambda = np.zeros((self.num_LM_eq,), dtype=ct.c_double, order='F')
        self.Lambda_dot = np.zeros((self.num_LM_eq,), dtype=ct.c_double, order='F')
        self.Lambda_ddot = np.zeros((self.num_LM_eq,), dtype=ct.c_double, order='F')

        if self.settings['write_lm']:
            dire = './output/' + self.data.settings['SHARPy']['case'] + '/NonLinearDynamicMultibody/'
            if not os.path.isdir(dire):
                os.makedirs(dire)
            self.fid_lambda = open(dire + 'lambda.dat', "w")
            self.fid_lambda_dot = open(dire + 'lambda_dot.dat', "w")
            self.fid_lambda_ddot = open(dire + 'lambda_ddot.dat', "w")
            self.fid_cond_num = open(dire + 'cond_num.dat', "w")

        # Define the number of dofs
        self.define_sys_size()

        self.prev_Dq = np.zeros((self.sys_size + self.num_LM_eq))

        self.settings['time_integrator_settings']['sys_size'] = self.sys_size
        self.settings['time_integrator_settings']['num_LM_eq'] = self.num_LM_eq

        # Initialise time integrator
        self.time_integrator = solver_interface.initialise_solver(
            self.settings['time_integrator'])
        self.time_integrator.initialise(
            self.data, self.settings['time_integrator_settings'])

    def add_step(self):
        self.data.structure.next_step()

    def next_step(self):
        pass

    def define_sys_size(self):
        """
        This function defines the number of degrees of freedom in a multibody systems

        Each body contributes with ``num_dof`` degrees of freedom and 10 more if the
        associated local FoR can move or has Lagrange Constraints associated
        """
        MBdict = self.data.structure.ini_mb_dict
        self.sys_size = self.data.structure.num_dof.value

        for ibody in range(self.data.structure.num_bodies):
            if (MBdict['body_%02d' % ibody]['FoR_movement'] == 'free'):
                self.sys_size += 10

    def assembly_MB_eq_system(self, MB_beam, MB_tstep, ts, dt, Lambda, Lambda_dot, MBdict):
        """
        This function generates the matrix and vector associated to the linear system to solve a structural iteration
        It usses a Newmark-beta scheme for time integration. Being M, C and K the mass, damping
        and stiffness matrices of the system:

        .. math::
            MB_Asys = MB_K + MB_C \frac{\gamma}{\beta dt} + \frac{1}{\beta dt^2} MB_M

        Args:
            MB_beam (list(:class:`~sharpy.structure.models.beam.Beam`)): each entry represents a body
            MB_tstep (list(:class:`~sharpy.utils.datastructures.StructTimeStepInfo`)): each entry represents a body
            ts (int): Time step number
            dt(int): time step
            Lambda (np.ndarray): Lagrange Multipliers array
            Lambda_dot (np.ndarray): Time derivarive of ``Lambda``
            MBdict (dict): Dictionary including the multibody information

        Returns:
            MB_Asys (np.ndarray): Matrix of the systems of equations
            MB_Q (np.ndarray): Vector of the systems of equations
        """
        self.num_LM_eq = lagrangeconstraints.define_num_LM_eq(self.lc_list)

        MB_M = np.zeros((self.sys_size, self.sys_size), dtype=ct.c_double, order='F')
        MB_C = np.zeros((self.sys_size, self.sys_size), dtype=ct.c_double, order='F')
        MB_K = np.zeros((self.sys_size, self.sys_size), dtype=ct.c_double, order='F')
        MB_Q = np.zeros((self.sys_size,), dtype=ct.c_double, order='F')
        first_dof = 0
        last_dof = 0

        # Loop through the different bodies
        for ibody in range(len(MB_beam)):

            # Initialize matrices
            M = None
            C = None
            K = None
            Q = None

            # Generate the matrices for each body
            if MB_beam[ibody].FoR_movement == 'prescribed':
                last_dof = first_dof + MB_beam[ibody].num_dof.value
                M, C, K, Q = xbeamlib.cbeam3_asbly_dynamic(MB_beam[ibody], MB_tstep[ibody], self.settings)

            elif MB_beam[ibody].FoR_movement == 'free':
                last_dof = first_dof + MB_beam[ibody].num_dof.value + 10
                M, C, K, Q = xbeamlib.xbeam3_asbly_dynamic(MB_beam[ibody], MB_tstep[ibody], self.settings)


            ############### Assembly into the global matrices
            # Flexible and RBM contribution to Asys
            MB_M[first_dof:last_dof, first_dof:last_dof] = M.astype(dtype=ct.c_double, copy=True, order='F')
            MB_C[first_dof:last_dof, first_dof:last_dof] = C.astype(dtype=ct.c_double, copy=True, order='F')
            MB_K[first_dof:last_dof, first_dof:last_dof] = K.astype(dtype=ct.c_double, copy=True, order='F')

            #Q
            MB_Q[first_dof:last_dof] = Q

            first_dof = last_dof

        # Define the number of equations
        # Generate matrices associated to Lagrange multipliers
        LM_C, LM_K, LM_Q = lagrangeconstraints.generate_lagrange_matrix(
            self.lc_list,
            MB_beam,
            MB_tstep,
            ts,
            self.num_LM_eq,
            self.sys_size,
            dt,
            Lambda,
            Lambda_dot,
            "dynamic")

        # Include the matrices associated to Lagrange Multipliers
        MB_C += LM_C[:self.sys_size, :self.sys_size]
        MB_K += LM_K[:self.sys_size, :self.sys_size]
        MB_Q += LM_Q[:self.sys_size]

        # Only working for non-holonomic constratints
        kBnh = LM_C[self.sys_size:, :self.sys_size]
        strict_LM_Q = LM_Q[self.sys_size:]

        return MB_M, MB_C, MB_K, MB_Q, kBnh, strict_LM_Q

    def integrate_position(self, MB_beam, MB_tstep, dt):
        """
        This function integrates the position of each local A FoR after the
        structural iteration has been solved.

        It uses a Newmark-beta approximation.

        Args:
            MB_beam (list(:class:`~sharpy.structure.models.beam.Beam`)): each entry represents a body
            MB_tstep (list(:class:`~sharpy.utils.datastructures.StructTimeStepInfo`)): each entry represents a body
            dt(int): time step
        """
        vel = np.zeros((6,),)
        acc = np.zeros((6,),)
        for ibody in range(0, len(MB_tstep)):
            # I think this is the right way to do it, but to make it match the rest I change it temporally
            if True:
                acc[0:3] = (0.5-self.beta)*np.dot(MB_beam[ibody].timestep_info.cga(),MB_beam[ibody].timestep_info.for_acc[0:3])+self.beta*np.dot(MB_tstep[ibody].cga(),MB_tstep[ibody].for_acc[0:3])
                vel[0:3] = np.dot(MB_beam[ibody].timestep_info.cga(),MB_beam[ibody].timestep_info.for_vel[0:3])
                MB_tstep[ibody].for_pos[0:3] += dt*(vel[0:3] + dt*acc[0:3])
            else:
                MB_tstep[ibody].for_pos[0:3] += dt*np.dot(MB_tstep[ibody].cga(),MB_tstep[ibody].for_vel[0:3])

    def extract_resultants(self):
        # TODO: code
        pass

    def compute_forces_constraints(self, MB_beam, MB_tstep, ts, dt, Lambda, Lambda_dot):
        """
        This function computes the forces generated at Lagrange Constraints

        Args:
            MB_beam (list(:class:`~sharpy.structure.models.beam.Beam`)): each entry represents a body
            MB_tstep (list(:class:`~sharpy.utils.datastructures.StructTimeStepInfo`)): each entry represents a body
            ts (int): Time step number
            dt(float): Time step increment
            Lambda (np.ndarray): Lagrange Multipliers array
            Lambda_dot (np.ndarray): Time derivarive of ``Lambda``

        Warning:
            This function is underdevelopment and not fully functional
        """
        try:
            self.lc_list[0]
        except IndexError:
            return

        # TODO the output of this routine is wrong. check at some point.
        LM_C, LM_K, LM_Q = lagrangeconstraints.generate_lagrange_matrix(self.lc_list, MB_beam, MB_tstep, ts, self.num_LM_eq, self.sys_size, dt, Lambda, Lambda_dot, "dynamic")
        F = -np.dot(LM_C[:, -self.num_LM_eq:], Lambda_dot) - np.dot(LM_K[:, -self.num_LM_eq:], Lambda)

        first_dof = 0
        for ibody in range(len(MB_beam)):
            # Forces associated to nodes
            body_numdof = MB_beam[ibody].num_dof.value
            body_freenodes = np.sum(MB_beam[ibody].vdof > -1)
            last_dof = first_dof + body_numdof
            MB_tstep[ibody].forces_constraints_nodes[(MB_beam[ibody].vdof > -1), :] = F[first_dof:last_dof].reshape(body_freenodes, 6, order='C')

            # Forces associated to the frame of reference
            if MB_beam[ibody].FoR_movement == 'free':
                # TODO: How are the forces in the quaternion equation interpreted?
                MB_tstep[ibody].forces_constraints_FoR[ibody, :] = F[last_dof:last_dof+10]
                last_dof += 10

            first_dof = last_dof
        # TODO: right now, these forces are only used as an output, they are not read when the multibody is splitted

    def write_lm_cond_num(self, iteration, Lambda, Lambda_dot, Lambda_ddot, cond_num, cond_num_lm):

        self.fid_lambda.write("%d %d " % (self.data.ts, iteration))
        self.fid_lambda_dot.write("%d %d " % (self.data.ts, iteration))
        self.fid_lambda_ddot.write("%d %d " % (self.data.ts, iteration))
        self.fid_cond_num.write("%d %d " % (self.data.ts, iteration))
        for ilm in range(self.num_LM_eq):
            self.fid_lambda.write("%f " % Lambda[ilm])
            self.fid_lambda_dot.write("%f " % Lambda_dot[ilm])
            self.fid_lambda_ddot.write("%f " % Lambda_ddot[ilm])
        self.fid_lambda.write("\n")
        self.fid_lambda_dot.write("\n")
        self.fid_lambda_ddot.write("\n")
        self.fid_cond_num.write("%e %e\n" % (cond_num, cond_num_lm))


    def run(self, structural_step=None, dt=None):
        if structural_step is None:
            structural_step = self.data.structure.timestep_info[-1]

        if structural_step.mb_dict is not None:
            MBdict = structural_step.mb_dict
        else:
            MBdict = self.data.structure.ini_mb_dict

        if dt is None:
            dt = self.settings['dt']
        else:
            self.settings['dt'] = dt

        if self.data.structure.ini_info.in_global_AFoR:
            self.data.structure.ini_info.whole_structure_to_local_AFoR(self.data.structure)

        if structural_step.in_global_AFoR:
            structural_step.whole_structure_to_local_AFoR(self.data.structure)

        self.num_LM_eq = lagrangeconstraints.define_num_LM_eq(self.lc_list)

        MB_beam, MB_tstep = mb.split_multibody(
            self.data.structure,
            structural_step,
            MBdict,
            self.data.ts)

        # Lagrange multipliers parameters
        num_LM_eq = self.num_LM_eq

        # Initialize
        q = np.zeros((self.sys_size + num_LM_eq,), dtype=ct.c_double, order='F')
        dqdt = np.zeros((self.sys_size + num_LM_eq,), dtype=ct.c_double, order='F')
        dqddt = np.zeros((self.sys_size + num_LM_eq,), dtype=ct.c_double, order='F')

        if not num_LM_eq == 0:
            Lambda = self.Lambda.astype(dtype=ct.c_double, copy=True, order='F')
            Lambda_dot = self.Lambda_dot.astype(dtype=ct.c_double, copy=True, order='F')
            Lambda_ddot = self.Lambda_ddot.astype(dtype=ct.c_double, copy=True, order='F')
        else:
            Lambda = 0
            Lambda_dot = 0

        # Predictor step
        q, dqdt, dqddt = mb.disp_and_accel2state(MB_beam, MB_tstep, Lambda, Lambda_dot, self.sys_size, num_LM_eq)
        self.time_integrator.predictor(q, dqdt, dqddt)

        # Reference residuals
        old_Dq = 1.0
        LM_old_Dq = 1.0

        converged = False
        for iteration in range(self.settings['max_iterations']):
            # Check if the maximum of iterations has been reached
            if iteration == self.settings['max_iterations'] - 1:
                error = ('Solver did not converge in %d iterations.\n res = %e \n LM_res = %e' %
                        (iteration, res, LM_res))
                raise exc.NotConvergedSolver(error)

            # Update positions and velocities
            Lambda, Lambda_dot = mb.state2disp_and_accel(q, dqdt, dqddt, MB_beam, MB_tstep, num_LM_eq)
            if self.settings['write_lm'] and iteration:
                self.write_lm_cond_num(iteration, Lambda, Lambda_dot, Lambda_ddot, cond_num, cond_num_lm)

            MB_M, MB_C, MB_K, MB_Q, kBnh, LM_Q = self.assembly_MB_eq_system(MB_beam,
                                                                MB_tstep,
                                                                self.data.ts,
                                                                dt,
                                                                Lambda,
                                                                Lambda_dot,
                                                                MBdict)

            Asys, Q = self.time_integrator.build_matrix(MB_M, MB_C, MB_K, MB_Q,
                                                        kBnh, LM_Q)

            if self.settings['write_lm']:
                cond_num = np.linalg.cond(Asys[:self.sys_size, :self.sys_size])
                cond_num_lm = np.linalg.cond(Asys)

            Dq = np.linalg.solve(Asys, -Q)

            # Evaluate convergence
            if iteration:
                res = np.max(np.abs(Dq[0:self.sys_size]))/old_Dq
                if np.isnan(res):
                    raise exc.NotConvergedSolver('Multibody res = NaN')
                if num_LM_eq:
                    LM_res = np.max(np.abs(Dq[self.sys_size:self.sys_size+num_LM_eq]))/LM_old_Dq
                else:
                    LM_res = 0.0
                if (res < self.settings['min_delta']) and (LM_res < self.settings['min_delta']):
                    converged = True

            # Relaxation
            relax_Dq = np.zeros_like(Dq)
            relax_Dq[:self.sys_size] = Dq[:self.sys_size].copy()
            relax_Dq[self.sys_size:] = ((1. - self.settings['relax_factor_lm'])*Dq[self.sys_size:] +
                                   self.settings['relax_factor_lm']*self.prev_Dq[self.sys_size:])
            self.prev_Dq = Dq.copy()

            # Corrector step
            self.time_integrator.corrector(q, dqdt, dqddt, relax_Dq)

            if converged:
                break

            if not iteration:
                old_Dq = np.max(np.abs(Dq[0:self.sys_size]))
                if num_LM_eq:
                    LM_old_Dq = np.max(np.abs(Dq[self.sys_size:self.sys_size+num_LM_eq]))

        Lambda, Lambda_dot = mb.state2disp_and_accel(q, dqdt, dqddt, MB_beam, MB_tstep, num_LM_eq)
        if self.settings['write_lm']:
            self.write_lm_cond_num(iteration, Lambda, Lambda_dot, Lambda_ddot, cond_num, cond_num_lm)
        # end: comment time stepping

        # End of Newmark-beta iterations
        # self.integrate_position(MB_beam, MB_tstep, dt)
        lagrangeconstraints.postprocess(self.lc_list, MB_beam, MB_tstep, "dynamic")
        self.compute_forces_constraints(MB_beam, MB_tstep, self.data.ts, dt, Lambda, Lambda_dot)
        if self.settings['gravity_on']:
            for ibody in range(len(MB_beam)):
                xbeamlib.cbeam3_correct_gravity_forces(MB_beam[ibody], MB_tstep[ibody], self.settings)
        mb.merge_multibody(MB_tstep, MB_beam, self.data.structure, structural_step, MBdict, dt)

        if not structural_step.in_global_AFoR:
            structural_step.whole_structure_to_global_AFoR(self.data.structure)

        self.Lambda = Lambda.astype(dtype=ct.c_double, copy=True, order='F')
        self.Lambda_dot = Lambda_dot.astype(dtype=ct.c_double, copy=True, order='F')
        self.Lambda_ddot = Lambda_ddot.astype(dtype=ct.c_double, copy=True, order='F')

        return self.data
