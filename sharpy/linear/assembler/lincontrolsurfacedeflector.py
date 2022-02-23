"""
Control surface deflector for linear systems
"""
import sharpy.linear.utils.ss_interface as ss_interface
import numpy as np
import sharpy.utils.algebra as algebra


@ss_interface.linear_system
class LinControlSurfaceDeflector(object):
    """
    Subsystem that deflects control surfaces for use with linear state space systems

    The current version supports only deflections. Future work will include standalone state-space systems to model
    physical actuators.

    """
    sys_id = 'LinControlSurfaceDeflector'
    
    settings_default = dict()
    settings_types = dict()
    settings_description = dict()

    def __init__(self):
        
        # Has the back bone structure for a future actuator model
        # As of now, it simply maps a deflection onto the aerodynamic grid by means of Kzeta_delta
        self.n_control_surfaces = 0
        self.Kzeta_delta = None  # type: np.ndarray
        self.Kdzeta_ddelta = None  # type: np.ndarray

        self.linuvlm = None  # type: sharpy.linear.src.linuvlm.Dynamic
        self.aero = None  # type: sharpy.aero.models.aerogrid.Aerogrid
        self.structure = None  # type: sharpy.structure.models.beam.Beam

        self.tsaero0 = None
        self.tsstruct0 = None

    def initialise(self, data, linuvlm):
        # Tasks:
        # 1 - Generic information
        #   * How many control surfaces (number of actual inputs)
        #   * How many uvlm surfaces
        self.n_control_surfaces = data.aero.n_control_surfaces

        self.linuvlm = linuvlm
        self.aero = data.aero
        self.structure = data.structure
        self.tsaero0 = data.aero.timestep_info[0]
        self.tsstruct0 = data.structure.timestep_info[0]

    def generate(self):
        """
        Generates a matrix mapping a linear control surface deflection onto the aerodynamic grid.

        This generates two matrices:

            * `Kzeta_delta` maps the deflection angle onto displacements. It has as many columns as independent control
              surfaces.

            * `Kdzeta_ddelta` maps the deflection rate onto grid velocities. Again, it has as many columns as
              independent control surfaces.

        Returns:
            tuple: Tuple containing `Kzeta_delta` and `Kdzeta_ddelta`.

        """
        # For future development
        # In hindsight, building this matrix iterating through structural node was a big mistake that
        # has led to very messy code. Would rework by element and in B frame

        aero = self.aero
        structure = self.structure
        linuvlm = self.linuvlm
        tsaero0 = self.tsaero0
        tsstruct0 = self.tsstruct0

        # Find the vertices corresponding to a control surface from beam coordinates to aerogrid
        aero_dict = aero.aero_dict
        n_surf = tsaero0.n_surf
        n_control_surfaces = self.n_control_surfaces

        Kdisp = np.zeros((3 * linuvlm.Kzeta, n_control_surfaces))
        Kvel = np.zeros((3 * linuvlm.Kzeta, n_control_surfaces))
        zeta0 = np.concatenate([tsaero0.zeta[i_surf].reshape(-1, order='C') for i_surf in range(n_surf)])

        Cga = algebra.quat2rotation(tsstruct0.quat).T
        Cag = Cga.T

        # Initialise these parameters
        hinge_axis = None  # Will be set once per control surface to the hinge axis
        with_control_surface = False  # Will be set to true if the spanwise node contains a control surface

        for global_node in range(structure.num_node):

            # Retrieve elements and local nodes to which a single node is attached
            for i_elem in range(structure.num_elem):
                if global_node in structure.connectivities[i_elem, :]:
                    i_local_node = np.where(structure.connectivities[i_elem, :] == global_node)[0][0]

                    for_delta = structure.frame_of_reference_delta[i_elem, :, 0]

                    # CRV to transform from G to B frame
                    psi = tsstruct0.psi[i_elem, i_local_node]
                    Cab = algebra.crv2rotation(psi)
                    Cba = Cab.T
                    Cbg = np.dot(Cab.T, Cag)
                    Cgb = Cbg.T

                    # Map onto aerodynamic coordinates. Some nodes may be part of two aerodynamic surfaces.
                    for structure2aero_node in aero.struct2aero_mapping[global_node]:
                        # Retrieve surface and span-wise coordinate
                        i_surf, i_node_span = structure2aero_node['i_surf'], structure2aero_node['i_n']

                        # Although a node may be part of 2 aerodynamic surfaces, we need to ensure that the current
                        # element for the given node is indeed part of that surface.
                        elems_in_surf = np.where(aero_dict['surface_distribution'] == i_surf)[0]
                        if i_elem not in elems_in_surf:
                            continue

                        # Surface panelling
                        M = aero.aero_dimensions[i_surf][0]
                        N = aero.aero_dimensions[i_surf][1]

                        K_zeta_start = 3 * sum(linuvlm.MS.KKzeta[:i_surf])
                        shape_zeta = (3, M + 1, N + 1)

                        i_control_surface = aero_dict['control_surface'][i_elem, i_local_node]
                        if i_control_surface >= 0:
                            if not with_control_surface:
                                i_start_of_cs = i_node_span.copy()
                                with_control_surface = True

                            control_surface_chord = aero_dict['control_surface_chord'][i_control_surface]

                            try:
                                control_surface_hinge_coord = \
                                    aero_dict['control_surface_hinge_coord'][i_control_surface] * \
                                    aero_dict['chord'][i_elem, i_local_node]
                            except KeyError:
                                control_surface_hinge_coord = None

                            i_node_hinge = M - control_surface_chord
                            i_vertex_hinge = [K_zeta_start +
                                              np.ravel_multi_index((i_axis, i_node_hinge, i_node_span), shape_zeta)
                                              for i_axis in range(3)]
                            i_vertex_next_hinge = [K_zeta_start +
                                                   np.ravel_multi_index((i_axis, i_node_hinge, i_start_of_cs + 1),
                                                                        shape_zeta) for i_axis in range(3)]

                            if control_surface_hinge_coord is not None and M == control_surface_chord:  # fully articulated control surface
                                zeta_hinge = Cgb.dot(Cba.dot(tsstruct0.pos[global_node]) + for_delta * np.array([0, control_surface_hinge_coord, 0]))
                                zeta_next_hinge = Cgb.dot(Cbg.dot(zeta_hinge) + np.array([1, 0, 0]))  # parallel to the x_b vector
                            else:
                                zeta_hinge = zeta0[i_vertex_hinge]
                                zeta_next_hinge = zeta0[i_vertex_next_hinge]

                            if hinge_axis is None:
                                # Hinge axis not yet set for current control surface
                                # Hinge axis is in G frame
                                hinge_axis = zeta_next_hinge - zeta_hinge
                                hinge_axis = hinge_axis / np.linalg.norm(hinge_axis)
                            for i_node_chord in range(M + 1):
                                i_vertex = [K_zeta_start +
                                            np.ravel_multi_index((i_axis, i_node_chord, i_node_span), shape_zeta)
                                            for i_axis in range(3)]

                                if i_node_chord >= i_node_hinge:
                                    # Zeta in G frame
                                    zeta_node = zeta0[i_vertex]  # Gframe
                                    chord_vec = (zeta_node - zeta_hinge)

                                    Kdisp[i_vertex, i_control_surface] = \
                                        Cgb.dot(der_R_arbitrary_axis_times_v(Cbg.dot(hinge_axis),
                                                                             0,
                                                                             -for_delta * Cbg.dot(chord_vec))) * for_delta * -1

                                    # Flap velocity
                                    Kvel[i_vertex, i_control_surface] = -algebra.skew(-for_delta * chord_vec).dot(
                                        hinge_axis) * for_delta * -1

                        else:
                            with_control_surface = False
                            hinge_axis = None  # Reset for next control surface

        # >>>> Merge control surfaces 0 and 1
        # Kdisp[:, 0] -= Kdisp[:, 1]
        # Kvel[:, 0] -= Kvel[:, 1]

        self.Kzeta_delta = Kdisp
        self.Kdzeta_ddelta = Kvel
        return Kdisp, Kvel


def der_Cx_by_v(delta, v):
    sd = np.sin(delta)
    cd = np.cos(delta)
    v2 = v[1]
    v3 = v[2]
    return np.array([0, -v2 * sd - v3 * cd, v2 * cd - v3 * sd])

def der_Cy_by_v(delta, v):
    s = np.sin(delta)
    c = np.cos(delta)
    v1 = v[0]
    v3 = v[2]
    return np.array([-s*v1 + v*v3, 0, -c*v1 - s*v3])


def der_R_arbitrary_axis_times_v(u, theta, v):
    r"""
    Linearised rotation vector of the vector ``v`` by angle ``theta`` about an arbitrary axis ``u``.

    The rotation of a vector :math:`\mathbf{v}` about the axis :math:`\mathbf{u}` by an
    angle :math:`\boldsymbol{\theta}` can be expressed as

    .. math:: \mathbf{w} = \mathbf{R}(\mathbf{u}, \theta) \mathbf{v},

    where :math:`\mathbf{R}` is a :math:`\mathbb{R}^{3\times 3}` matrix.

    This expression can be linearised for it to be included in the linear solver as

    .. math:: \delta\mathbf{w} = \frac{\partial}{\partial\theta}\left(\mathbf{R}(\mathbf{u}, \theta_0)\right)\delta\theta

    The matrix :math:`\mathbf{R}` is

    .. math::

        \mathbf{R} =
        \begin{bmatrix}\cos \theta +u_{x}^{2}\left(1-\cos \theta \right) &
        u_{x}u_{y}\left(1-\cos \theta \right)-u_{z}\sin \theta &
        u_{x}u_{z}\left(1-\cos \theta \right)+u_{y}\sin \theta \\
        u_{y}u_{x}\left(1-\cos \theta \right)+u_{z}\sin \theta &
        \cos \theta +u_{y}^{2}\left(1-\cos \theta \right)&
        u_{y}u_{z}\left(1-\cos \theta \right)-u_{x}\sin \theta \\
        u_{z}u_{x}\left(1-\cos \theta \right)-u_{y}\sin \theta &
        u_{z}u_{y}\left(1-\cos \theta \right)+u_{x}\sin \theta &
        \cos \theta +u_{z}^{2}\left(1-\cos \theta \right)\end{bmatrix},

    and its linearised expression becomes

    .. math::

        \frac{\partial}{\partial\theta}\left(\mathbf{R}(\mathbf{u}, \theta_0)\right) =
        \begin{bmatrix}
        -\sin \theta +u_{x}^{2}\sin \theta \mathbf{v}_1 +
        u_{x}u_{y}\sin \theta-u_{z} \cos \theta \mathbf{v}_2 +
        u_{x}u_{z}\sin \theta +u_{y}\cos \theta \mathbf{v}_3 \\
        u_{y}u_{x}\sin \theta+u_{z}\cos \theta\mathbf{v}_1
        -\sin \theta +u_{y}^{2}\sin \theta\mathbf{v}_2 +
        u_{y}u_{z}\sin \theta-u_{x}\cos \theta\mathbf{v}_3 \\
        u_{z}u_{x}\sin \theta-u_{y}\cos \theta\mathbf{v}_1 +
        u_{z}u_{y}\sin \theta+u_{x}\cos \theta\mathbf{v}_2
        -\sin \theta +u_{z}^{2}\sin\theta\mathbf{v}_3\end{bmatrix}_{\theta=\theta_0}

    and is of dimension :math:`\mathbb{R}^{3\times 1}`.

    Args:
        u (numpy.ndarray): Arbitrary rotation axis
        theta (float): Rotation angle (radians)
        v (numpy.ndarray): Vector to rotate

    Returns:
        numpy.ndarray: Linearised rotation vector of dimensions :math:`\mathbb{R}^{3\times 1}`.
    """

    u = u / np.linalg.norm(u)
    c = np.cos(theta)
    s = np.sin(theta)

    ux, uy, uz = u
    v1, v2, v3 = v

    dR11 = -s + ux ** 2 * s
    dR12 = ux * uy * s - uz * c
    dR13 = ux * uz * s + uy * c

    dR21 = uy * ux * s + uz * c
    dR22 = -s + uy ** 2 * s
    dR23 = uy * uz * s - ux * c

    dR31 = uz * ux * s - uy * c
    dR32 = uz * uy * s + ux * c
    dR33 = -s + uz ** 2

    dRv = np.zeros((3, ))
    dRv[0] = dR11 * v1 + dR12 * v2 + dR13 * v3
    dRv[1] = dR21 * v1 + dR22 * v2 + dR23 * v3
    dRv[2] = dR31 * v1 + dR32 * v2 + dR33 * v3

    return dRv

