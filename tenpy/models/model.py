"""This module contains some base classes for models.

A 'model' is supposed to represent a Hamiltonian in a generalized way.
The :class:`~tenpy.models.lattice.Lattice` specifies the geometry and
underlying Hilbert space, and is thus common to all models.
It is needed to intialize the common base class :class:`Model` of all models.

Different algorithms require different representations of the Hamiltonian.
For example for DMRG, the Hamiltonian needs to be given as an MPO,
while TEBD needs the Hamiltonian to be represented by 'nearest neighbor' bond terms.
This module contains the base classes defining these possible representations,
namley the :class:`MPOModel` and :class:`NearestNeighborModel`.

A particular model like the :class:`~tenpy.models.models.xxz_chain.XXZChain` should then
yet another class derived from these classes. In it's __init__, it needs to explicitly call
the ``MPOModel.__init__(self, lattice, H_MPO)``, providing an MPO representation of H,
and also the ``NearestNeighborModel.__init__(self, lattice, H_bond)``,
providing a representation of H by bond terms `H_bond`.

The :class:`CouplingModel` is the attempt to generalize the representation of `H`
by explicitly specifying the couplings in a general way, and providing functionality
for converting them into `H_MPO` and `H_bond`.
This allows to quickly generate new model classes for a very broad class of Hamiltonians.

For simplicity, the :class:`CouplingModel` is limited to interactions involving only two sites.
Yet, we also provide the :class:`MultiCouplingModel` to generate Models for Hamiltonians
involving couplings between multiple sites.

The :class:`CouplingMPOModel` aims at structuring the initialization for most models and is used
as base class in (most of) the predefined models in TeNPy.

See also the introduction in :doc:`/intro/model`.
"""
# Copyright 2018-2020 TeNPy Developers, GNU GPLv3

import numpy as np
import warnings

from .lattice import get_lattice, Lattice, TrivialLattice
from ..linalg import np_conserved as npc
from ..linalg.charges import QTYPE, LegCharge
from ..tools.misc import to_array, add_with_None_0
from ..tools.params import asConfig
from ..networks import mpo  # used to construct the Hamiltonian as MPO
from ..networks.terms import OnsiteTerms, CouplingTerms, MultiCouplingTerms
from ..networks.terms import order_combine_term
from ..networks.site import group_sites
from ..tools.hdf5_io import Hdf5Exportable

__all__ = [
    'Model', 'NearestNeighborModel', 'MPOModel', 'CouplingModel', 'MultiCouplingModel',
    'CouplingMPOModel'
]

_DEPRECATED_ARG_NOT_SET = "DEPRECATED"


class Model(Hdf5Exportable):
    """Base class for all models.

    The common base to all models is the underlying Hilbert space and geometry, specified by a
    :class:`~tenpy.model.lattice.Lattice`.

    Parameters
    ----------
    lattice : :class:`~tenpy.model.lattice.Lattice`
        The lattice defining the geometry and the local Hilbert space(s).

    Attributes
    ----------
    lat : :class:`~tenpy.model.lattice.Lattice`
        The lattice defining the geometry and the local Hilbert space(s).
    """
    def __init__(self, lattice):
        # NOTE: every subclass like CouplingModel, MPOModel, NearestNeighborModel calls this
        # __init__, so it gets called multiple times when a user implements e.g. a
        # class MyModel(CouplingModel, NearestNeighborModel, MPOModel).
        if not hasattr(self, 'lat'):
            # first call: initialize everything
            self.lat = lattice
        else:
            # Model.__init__() got called before
            if self.lat is not lattice:  # expect the *same instance*!
                raise ValueError("Model.__init__() called with different lattice instances.")

    def enlarge_mps_unit_cell(self, factor=2):
        """Repeat the unit cell for infinite MPS boundary conditions; in place.

        This has to be done after finishing initialization and can not be reverted.

        Parameters
        ----------
        factor : int
            The new number of sites in the MPS unit cell will be increased from `N_sites` to
            ``factor*N_sites_per_ring``. Since MPS unit cells are repeated in the `x`-direction
            in our convetion, the lattice shape goes from
            ``(Lx, Ly, ..., Lu)`` to ``(Lx*factor, Ly, ..., Lu)``.
        """
        self.lat.enlarge_mps_unit_cell(factor)

    def group_sites(self, n=2, grouped_sites=None):
        """Modify `self` in place to group sites.

        Group each `n` sites together using the :class:`~tenpy.networks.site.GroupedSite`.
        This might allow to do TEBD with a Trotter decomposition,
        or help the convergence of DMRG (in case of too long range interactions).

        This has to be done after finishing initialization and can not be reverted.

        .. todo :
            We could actually keep the lattice structure if the order is (default) Cstyle.

        Parameters
        ----------
        n : int
            Number of sites to be grouped together.
        grouped_sites : None | list of :class:`~tenpy.networks.site.GroupedSite`
            The sites grouped together.

        Returns
        -------
        grouped_sites : list of :class:`~tenpy.networks.site.GroupedSite`
            The sites grouped together.
        """
        if grouped_sites is None:
            grouped_sites = group_sites(self.lat.mps_sites(), n, charges='same')
        else:
            assert grouped_sites[0].n_sites == n
        self.lat = TrivialLattice(grouped_sites, bc_MPS=self.lat.bc_MPS, bc='periodic')
        return grouped_sites


class NearestNeighborModel(Model):
    r"""Base class for a model of nearest neigbor interactions w.r.t. the MPS index.

    In this class, the Hamiltonian :math:`H = \sum_{i} H_{i,i+1}` is represented by
    "bond terms" :math:`H_{i,i+1}` acting only on two neighboring sites `i` and `i+1`,
    where `i` is an integer.
    Instances of this class are suitable for :mod:`~tenpy.algorithms.tebd`.

    Note that the "nearest-neighbor" in the name refers to the MPS index, not the lattice.
    In short, this works only for 1-dimensional (1D) nearest-neighbor models:
    A 2D lattice is internally mapped to a 1D MPS "snake", and even a nearest-neighbor coupling
    in 2D becomes long-range in the MPS chain.



    Parameters
    ----------
    lattice : :class:`tenpy.model.lattice.Lattice`
        The lattice defining the geometry and the local Hilbert space(s).
    H_bond : list of {:class:`~tenpy.linalg.np_conserved.Array` | None}
        The Hamiltonian rewritten as ``sum_i H_bond[i]`` for MPS indices ``i``.
        ``H_bond[i]`` acts on sites ``(i-1, i)``; we require ``len(H_bond) == lat.N_sites``.
        Legs of each ``H_bond[i]`` are ``['p0', 'p0*', 'p1', 'p1*']``.

    Attributes
    ----------
    H_bond : list of {:class:`~tenpy.linalg.np_conserved.Array` | None}
        The Hamiltonian rewritten as ``sum_i H_bond[i]`` for MPS indices ``i``.
        ``H_bond[i]`` acts on sites ``(i-1, i)``, ``None`` represents 0.
        Legs of each ``H_bond[i]`` are ``['p0', 'p0*', 'p1', 'p1*']``.
        `H_bond` is not affected by the `explicit_plus_hc` flag of a :class:`CouplingModel`.
    """
    def __init__(self, lattice, H_bond):
        Model.__init__(self, lattice)
        self.H_bond = list(H_bond)
        if self.lat.bc_MPS != 'infinite':
            assert self.H_bond[0] is None
        NearestNeighborModel.test_sanity(self)
        # like self.test_sanity(), but use the version defined below even for derived class

    @classmethod
    def from_MPOModel(cls, mpo_model):
        """Initialize a NearestNeighborModel from a model class defining an MPO.

        This is especially usefull in combination with :meth:`MPOModel.group_sites`.

        Parameters
        ----------
        mpo_model : :class:`MPOModel`
            A model instance implementing the MPO.
            Does not need to be a :class:`NearestNeighborModel`, but should only have
            nearest-neighbor couplings.

        Examples
        --------
        The `SpinChainNNN2` has next-nearest-neighbor couplings and thus only implements an MPO:

        >>> from tenpy.models.spins_nnn import SpinChainNNN2
        >>> nnn_chain = SpinChainNNN2({'L': 20})
        parameter 'L'=20 for SpinChainNNN2
        >>> print(isinstance(nnn_chain, NearestNeighborModel))
        False
        >>> print("range before grouping:", nnn_chain.H_MPO.max_range)
        range before grouping: 2

        By grouping each two neighboring sites, we can bring it down to nearest neighbors.

        >>> nnn_chain.group_sites(2)
        >>> print("range after grouping:", nnn_chain.H_MPO.max_range)
        range after grouping: 1

        Yet, TEBD will not yet work, as the model doesn't define `H_bond`.
        However, we can initialize a NearestNeighborModel from the MPO:

        >>> nnn_chain_for_tebd = NearestNeighborModel.from_MPOModel(nnn_chain)
        """
        return cls(mpo_model.lat, mpo_model.calc_H_bond_from_MPO())

    def test_sanity(self):
        if len(self.H_bond) != self.lat.N_sites:
            raise ValueError("wrong len of H_bond")

    def trivial_like_NNModel(self):
        """Return a NearestNeighborModel with same lattice, but trivial (H=0) bonds."""
        triv_H = [H.zeros_like() if H is not None else None for H in self.H_bond]
        return NearestNeighborModel(self.lat, triv_H)

    def bond_energies(self, psi):
        """Calculate bond energies <psi|H_bond|psi>.

        Parameters
        ----------
        psi : :class:`~tenpy.networks.mps.MPS`
            The MPS for which the bond energies should be calculated.

        Returns
        -------
        E_bond : 1D ndarray
            List of bond energies: for finite bc, ``E_Bond[i]`` is the energy of bond ``i, i+1``.
            (i.e. we omit bond 0 between sites L-1 and 0);
            for infinite bc ``E_bond[i]`` is the energy of bond ``i-1, i``.
        """
        if self.lat.bc_MPS == 'infinite':
            return psi.expectation_value(self.H_bond, axes=(['p0', 'p1'], ['p0*', 'p1*']))
        # else
        return psi.expectation_value(self.H_bond[1:], axes=(['p0', 'p1'], ['p0*', 'p1*']))

    def enlarge_mps_unit_cell(self, factor=2):
        """Repeat the unit cell for infinite MPS boundary conditions; in place.

        This has to be done after finishing initialization and can not be reverted.

        Parameters
        ----------
        factor : int
            The new number of sites in the MPS unit cell will be increased from `N_sites` to
            ``factor*N_sites_per_ring``. Since MPS unit cells are repeated in the `x`-direction
            in our convetion, the lattice shape goes from
            ``(Lx, Ly, ..., Lu)`` to ``(Lx*factor, Ly, ..., Lu)``.
        """
        super().enlarge_mps_unit_cell(factor)
        self.H_bond = self.H_bond * factor

    def group_sites(self, n=2, grouped_sites=None):
        """Modify `self` in place to group sites.

        Group each `n` sites together using the :class:`~tenpy.networks.site.GroupedSite`.
        This might allow to do TEBD with a Trotter decomposition,
        or help the convergence of DMRG (in case of too long range interactions).

        This has to be done after finishing initialization and can not be reverted.

        Parameters
        ----------
        n : int
            Number of sites to be grouped together.
        grouped_sites : None | list of :class:`~tenpy.networks.site.GroupedSite`
            The sites grouped together.

        Returns
        -------
        grouped_sites : list of :class:`~tenpy.networks.site.GroupedSite`
            The sites grouped together.
        """
        grouped_sites = super().group_sites(n, grouped_sites)
        old_L = len(self.H_bond)
        new_L = len(grouped_sites)
        finite = self.H_bond[0] is None
        H_bond = [None] * new_L
        i = 0  # old index
        for k, gs in enumerate(grouped_sites):
            # calculate new_Hb on bond (k, k+1)
            k2 = (k + 1) % new_L
            next_gs = grouped_sites[k2]
            new_H_onsite = None  # collect old H_bond terms inside `gs`
            for j in range(1, gs.n_sites):
                old_Hb = self.H_bond[(i + j) % old_L]
                add_H_onsite = self._group_sites_Hb_to_onsite(gs, j, old_Hb)
                new_H_onsite = add_with_None_0(new_H_onsite, add_H_onsite)
            old_Hb = self.H_bond[(i + gs.n_sites) % old_L]
            new_Hb = self._group_sites_Hb_to_bond(gs, next_gs, old_Hb)
            if new_H_onsite is not None:
                if k + 1 != new_L or not finite:
                    # infinite or in the bulk: add new_H_onsite to new_Hb
                    add_Hb = npc.outer(new_H_onsite, next_gs.Id.transpose(['p', 'p*']))
                    new_Hb = add_with_None_0(new_Hb, add_Hb)
                else:  # finite and k = new_L - 1
                    # the new_H_onsite needs to be added to the right-most Hb
                    prev_gs = grouped_sites[k - 1]
                    add_Hb = npc.outer(prev_gs.Id.transpose(['p', 'p*']), new_H_onsite)
                    H_bond[-1] = add_with_None_0(H_bond[-1], add_Hb)
            H_bond[k2] = add_with_None_0(H_bond[k2], new_Hb)
            i += gs.n_sites
        for Hb in H_bond:
            if Hb is None:
                continue
            Hb.iset_leg_labels(['p0', 'p0*', 'p1', 'p1*']).itranspose(['p0', 'p1', 'p0*', 'p1*'])
        self.H_bond = H_bond
        return grouped_sites

    def _group_sites_Hb_to_onsite(self, gr_site, j, old_Hb):
        """kroneckerproduct for H_bond term within a GroupedSite.

        `old_Hb` acts on sites (j-1, j) of `gr_sites`.
        """
        if old_Hb is None:
            return None
        old_Hb = old_Hb.transpose(['p0', 'p0*', 'p1', 'p1*'])
        ops = [s.Id
               for s in gr_site.sites[:j - 1]] + [old_Hb] + [s.Id for s in gr_site.sites[j + 1:]]
        Hb = ops[0]
        for op in ops[1:]:
            Hb = npc.outer(Hb, op)
        combine = [list(range(0, 2 * gr_site.n_sites, 2)), list(range(1, 2 * gr_site.n_sites, 2))]
        pipe = gr_site.leg
        Hb = Hb.combine_legs(combine, pipes=[pipe, pipe.conj()])
        return Hb  # labels would be 'p', 'p*' w.r.t. gr_site.

    def _group_sites_Hb_to_bond(self, gr_site_L, gr_site_R, old_Hb):
        """Kroneckerproduct for H_bond term acting on two GroupedSites.

        `old_Hb` acts on the right-most site of `gr_site_L` and left-most site of `gr_site_R`.
        """
        if old_Hb is None:
            return None
        old_Hb = old_Hb.transpose(['p0', 'p0*', 'p1', 'p1*'])
        ops = [s.Id for s in gr_site_L.sites[:-1]] + [old_Hb] + [s.Id for s in gr_site_R.sites[1:]]
        Hb = ops[0]
        for op in ops[1:]:
            Hb = npc.outer(Hb, op)
        NL, NR = gr_site_L.n_sites, gr_site_R.n_sites
        pipeL, pipeR = gr_site_L.leg, gr_site_R.leg
        combine = [
            list(range(0, 2 * NL, 2)),
            list(range(1, 2 * NL, 2)),
            list(range(2 * NL, 2 * (NL + NR), 2)),
            list(range(2 * NL + 1, 2 * (NL + NR), 2))
        ]
        Hb = Hb.combine_legs(combine, pipes=[pipeL, pipeL.conj(), pipeR, pipeR.conj()])
        return Hb  # labels would be 'p0', 'p0*', 'p1', 'p1*' w.r.t. gr_site_{L,R}

    def calc_H_MPO_from_bond(self, tol_zero=1.e-15):
        """Calculate the MPO Hamiltonian from the bond Hamiltonian.

        Parameters
        ----------
        tol_zero : float
            Arrays with norm < `tol_zero` are considered to be zero.

        Returns
        -------
        H_MPO : :class:`~tenpy.networks.mpo.MPO`
            MPO representation of the Hamiltonian.
        """
        H_bond = self.H_bond  # entry i acts on sites (i-1,i)
        dtype = np.find_common_type([Hb.dtype for Hb in H_bond if Hb is not None], [])
        bc = self.lat.bc_MPS
        sites = self.lat.mps_sites()
        L = len(sites)
        onsite_terms = [None] * L  # onsite terms on each site `i`
        bond_XYZ = [None] * L  # svd of couplings on each bond (i-1, i)
        chis = [2] * (L + 1)
        assert len(self.H_bond) == L
        for i, Hb in enumerate(H_bond):
            if Hb is None:
                continue
            j = (i - 1) % L
            Hb = Hb.transpose(['p0', 'p0*', 'p1', 'p1*'])
            d_L, d_R = sites[j].dim, sites[i].dim  # dimension of local hilbert space:
            Id_L, Id_R = sites[i].Id, sites[j].Id
            # project on onsite-terms by contracting with identities; Tr(Id_{L/R}) = d_{L/R}
            onsite_L = npc.tensordot(Hb, Id_R, axes=(['p1', 'p1*'], ['p*', 'p'])) / d_R
            if npc.norm(onsite_L) > tol_zero:
                Hb -= npc.outer(onsite_L, Id_R)
                onsite_terms[j] = add_with_None_0(onsite_terms[j], onsite_L)
            onsite_R = npc.tensordot(Id_L, Hb, axes=(['p*', 'p'], ['p0', 'p0*'])) / d_L
            if npc.norm(onsite_R) > tol_zero:
                Hb -= npc.outer(Id_L, onsite_R)
                onsite_terms[i] = add_with_None_0(onsite_terms[i], onsite_R)
            if npc.norm(Hb) < tol_zero:
                continue
            Hb = Hb.combine_legs([['p0', 'p0*'], ['p1', 'p1*']])
            chinfo = Hb.chinfo
            qtotal = [chinfo.make_valid(), chinfo.make_valid()]  # zero charge
            X, Y, Z = npc.svd(Hb, cutoff=tol_zero, inner_labels=['wR', 'wL'], qtotal_LR=qtotal)
            assert len(Y) > 0
            chis[i] = len(Y) + 2
            X = X.split_legs([0])
            YZ = Z.iscale_axis(Y, axis=0).split_legs([1])
            bond_XYZ[i] = (X, YZ)
            chinfo = Hb.chinfo
        # construct the legs
        legs = [None] * (L + 1)  # legs[i] is leg 'wL' left of site i with qconj=+1
        for i in range(L + 1):
            if i == L and bc == 'infinite':
                legs[i] = legs[0]
                break
            chi = chis[i]
            qflat = np.zeros((chi, chinfo.qnumber), dtype=QTYPE)
            if chi > 2:
                YZ = bond_XYZ[i][1]
                qflat[1:-1, :] = Z.legs[0].to_qflat()
            leg = LegCharge.from_qflat(chinfo, qflat, qconj=+1)
            legs[i] = leg
        # now construct the W tensors
        Ws = [None] * L
        for i in range(L):
            wL, wR = legs[i], legs[i + 1].conj()
            p = sites[i].leg
            W = npc.zeros([wL, wR, p, p.conj()], dtype, labels=['wL', 'wR', 'p', 'p*'])
            W[0, 0, :, :] = sites[i].Id
            W[-1, -1, :, :] = sites[i].Id
            onsite = onsite_terms[i]
            if onsite is not None:
                W[0, -1, :, :] = onsite
            if bond_XYZ[i] is not None:
                _, YZ = bond_XYZ[i]
                W[1:-1, -1, :, :] = YZ.itranspose(['wL', 'p1', 'p1*'])
            j = (i + 1) % L
            if bond_XYZ[j] is not None:
                X, _ = bond_XYZ[j]
                W[0, 1:-1, :, :] = X.itranspose(['wR', 'p0', 'p0*'])
            Ws[i] = W
        H_MPO = mpo.MPO(sites, Ws, bc, 0, -1, max_range=2)
        return H_MPO


class MPOModel(Model):
    """Base class for a model with an MPO representation of the Hamiltonian.

    In this class, the Hamiltonian gets represented by an :class:`~tenpy.networks.mpo.MPO`.
    Thus, instances of this class are suitable for MPO-based algorithms like DMRG
    :mod:`~tenpy.algorithms.dmrg` and MPO time evolution.

    .. todo ::
        implement MPO for time evolution...

    Parameters
    ----------
    H_MPO : :class:`~tenpy.networks.mpo.MPO`
        The Hamiltonian rewritten as an MPO.

    Attributes
    ----------
    H_MPO : :class:`tenpy.networks.mpo.MPO`
        MPO representation of the Hamiltonian. If the `explicit_plus_hc` flag of the MPO is `True`,
        the represented Hamiltonian is ``H_MPO + hermitian_cojugate(H_MPO)``.
    """
    def __init__(self, lattice, H_MPO):
        Model.__init__(self, lattice)
        self.H_MPO = H_MPO
        MPOModel.test_sanity(self)
        # like self.test_sanity(), but use the version defined below even for derived class

    def test_sanity(self):
        if self.H_MPO.sites != self.lat.mps_sites():
            raise ValueError("lattice incompatible with H_MPO.sites")

    def enlarge_mps_unit_cell(self, factor=2):
        """Repeat the unit cell for infinite MPS boundary conditions; in place.

        This has to be done after finishing initialization and can not be reverted.

        Parameters
        ----------
        factor : int
            The new number of sites in the MPS unit cell will be increased from `N_sites` to
            ``factor*N_sites_per_ring``. Since MPS unit cells are repeated in the `x`-direction
            in our convetion, the lattice shape goes from
            ``(Lx, Ly, ..., Lu)`` to ``(Lx*factor, Ly, ..., Lu)``.
        """
        super().enlarge_mps_unit_cell(factor)
        self.H_MPO.enlarge_mps_unit_cell(factor)

    def group_sites(self, n=2, grouped_sites=None):
        """Modify `self` in place to group sites.

        Group each `n` sites together using the :class:`~tenpy.networks.site.GroupedSite`.
        This might allow to do TEBD with a Trotter decomposition,
        or help the convergence of DMRG (in case of too long range interactions).

        This has to be done after finishing initialization and can not be reverted.

        Parameters
        ----------
        n : int
            Number of sites to be grouped together.
        grouped_sites : None | list of :class:`~tenpy.networks.site.GroupedSite`
            The sites grouped together.

        Returns
        -------
        grouped_sites : list of :class:`~tenpy.networks.site.GroupedSite`
            The sites grouped together.
        """
        grouped_sites = super().group_sites(n, grouped_sites)
        self.H_MPO.group_sites(n, grouped_sites)
        return grouped_sites

    def calc_H_bond_from_MPO(self, tol_zero=1.e-15):
        """Calculate the bond Hamiltonian from the MPO Hamiltonian.

        Parameters
        ----------
        tol_zero : float
            Arrays with norm < `tol_zero` are considered to be zero.

        Returns
        -------
        H_bond : list of :class:`~tenpy.linalg.np_conserved.Array`
            Bond terms as required by the constructor of :class:`NearestNeighborModel`.
            Legs are ``['p0', 'p0*', 'p1', 'p1*']``

        Raises
        ------
        ValueError : if the Hamiltonian contains longer-range terms.
        """
        H_MPO = self.H_MPO
        sites = H_MPO.sites
        finite = H_MPO.finite
        L = H_MPO.L
        Ws = [H_MPO.get_W(i, copy=True) for i in range(L)]
        # Copy of Ws: we set everything to zero, which we take out and add to H_bond, such that
        # we can check that Ws is zero in the end to ensure that H didn't have long range couplings
        H_onsite = [None] * L
        H_bond = [None] * L
        # first take out onsite terms and identities
        for i, W in enumerate(Ws):
            # bond `a` is left of site i, bond `b` is right
            IdL_a = H_MPO.IdL[i]
            IdR_a = H_MPO.IdR[i]
            IdL_b = H_MPO.IdL[i + 1]
            IdR_b = H_MPO.IdR[i + 1]
            W.itranspose(['wL', 'wR', 'p', 'p*'])
            H_onsite[i] = W[IdL_a, IdR_b, :, :]
            W[IdL_a, IdR_b, :, :] *= 0
            # remove Identities
            if IdR_a is not None:
                W[IdR_a, IdR_b, :, :] *= 0.
            if IdL_b is not None:
                W[IdL_a, IdL_b, :, :] *= 0.
        # now multiply together the bonds
        for j, Wj in enumerate(Ws):
            # for bond (i, j) == (j-1, j) == (i, i+1)
            if finite and j == 0:
                continue
            i = (j - 1) % L
            Wi = Ws[i]
            IdL_a = H_MPO.IdL[i]
            IdR_c = H_MPO.IdR[j + 1]
            Hb = npc.tensordot(Wi[IdL_a, :, :, :], Wj[:, IdR_c, :, :], axes=('wR', 'wL'))
            Wi[IdL_a, :, :, :] *= 0.
            Wj[:, IdR_c, :, :] *= 0.
            # Hb has legs p0, p0*, p1, p1*
            H_bond[j] = Hb
        # check that nothing is left
        for W in Ws:
            if npc.norm(W) > tol_zero:
                raise ValueError("Bond couplings didn't capture everything. "
                                 "Either H is long range or IdL/IdR is wrong!")
        # now merge the onsite terms to H_bond
        for j in range(L):
            if finite and j == 0:
                continue
            i = (j - 1) % L
            strength_i = 1. if finite and i == 0 else 0.5
            strength_j = 1. if finite and j == L - 1 else 0.5
            Hb = (npc.outer(sites[i].Id, strength_j * H_onsite[j]) +
                  npc.outer(strength_i * H_onsite[i], sites[j].Id))
            Hb = add_with_None_0(H_bond[j], Hb)
            Hb.iset_leg_labels(['p0', 'p0*', 'p1', 'p1*'])
            H_bond[j] = Hb
        if finite:
            assert H_bond[0] is None
        if self.explicit_plus_hc:
            # represented H = H_MPO + h.c.
            # so we need to explicitly add the hermitian conjugate terms
            for i, Hb in enumerate(H_bond):
                if Hb is not None:
                    H_bond[i] = Hb + Hb.conj().itranspose(Hb.get_leg_labels())
        return H_bond


class CouplingModel(Model):
    """Base class for a general model of a Hamiltonian consisting of two-site couplings.

    In this class, the terms of the Hamiltonian are specified explicitly as
    :class:`~tenpy.networks.terms.OnsiteTerms` or :class:`~tenpy.networks.terms.CouplingTerms`.

    .. deprecated:: 0.4.0
        `bc_coupling` will be removed in 1.0.0. To specify the full geometry in the lattice,
        use the `bc` parameter of the :class:`~tenpy.model.latttice.Lattice`.

    Parameters
    ----------
    lattice : :class:`~tenpy.model.lattice.Lattice`
        The lattice defining the geometry and the local Hilbert space(s).
    bc_coupling : (iterable of) {``'open'`` | ``'periodic'`` | ``int``}
        Boundary conditions of the couplings in each direction of the lattice. Defines how the
        couplings are added in :meth:`add_coupling`. A single string holds for all directions.
        An integer `shift` means that we have periodic boundary conditions along this direction,
        but shift/tilt by ``-shift*lattice.basis[0]`` (~cylinder axis for ``bc_MPS='infinite'``)
        when going around the boundary along this direction.
    explicit_plus_hc : bool
        If True, the Hermitian conjugate of the MPO is computed at runtime,
        rather than saved in the MPO.

    Attributes
    ----------
    onsite_terms : {'category': :class:`~tenpy.networks.terms.OnsiteTerms`}
        The :class:`~tenpy.networks.terms.OnsiteTerms` ordered by category.
    coupling_terms : {'category': :class:`~tenpy.networks.terms.CouplingTerms`}
        The :class:`~tenpy.networks.terms.CouplingTerms` ordered by category.
        In a :class:`MultiCouplingModel`, values may also be
        :class:`~tenpy.networks.terms.MultiCouplingTerms`.
    explicit_plus_hc : bool
        If `True`, `self` represents the terms in :attr:`onsite_terms` and :attr:`coupling_terms`
        *and* their hermitian conjugate added. The flag will be carried on the MPO, which will
        have a reduced bond dimension if ``self.add_coupling(..., plus_hc=True)`` was used.
        Note that :meth:`add_onsite` and :meth:`add_coupling` respect this flag, ensuring that the
        *represented* Hamiltonian is indepentent of the `explicit_plus_hc` flag.
    """
    def __init__(self, lattice, bc_coupling=None, explicit_plus_hc=False):
        Model.__init__(self, lattice)
        if bc_coupling is not None:
            warnings.warn("`bc_coupling` in CouplingModel: use `bc` in Lattice instead",
                          FutureWarning,
                          stacklevel=2)
            lattice._set_bc(bc_coupling)
        L = self.lat.N_sites
        self.onsite_terms = {}
        self.coupling_terms = {}
        self.explicit_plus_hc = explicit_plus_hc
        CouplingModel.test_sanity(self)
        # like self.test_sanity(), but use the version defined below even for derived class

    def test_sanity(self):
        """Sanity check, raises ValueErrors, if something is wrong."""
        sites = self.lat.mps_sites()
        for ot in self.onsite_terms.values():
            ot._test_terms(sites)
        for ct in self.coupling_terms.values():
            ct._test_terms(sites)

    def add_local_term(self, strength, term, category=None, plus_hc=False):
        """Add a single term to `self`.

        The repesented term is `strength` times the product of the operators given in `terms`.
        Each operator is specified by the name and the site it acts on; the latter given by
        a lattice index, see :class:`~tenpy.models.lattice.Lattice`.

        Depending on the length of `term`, it can add an onsite term or a coupling term to
        :attr:`onsite_terms` or :attr:`coupling_terms`, respectively.

        Parameters
        ----------
        strength : float/complex
            The prefactor of the term.
        term : list of (str, array_like)
            List of tuples ``(opname, lat_idx)`` where `opname` is a string describing the operator
            acting on the site given by the lattice index `lat_idx`. Here, `lat_idx` is for
            example `[x, y, u]` for a 2D lattice, with `u` being the index within the unit cell.
        category:
            Descriptive name used as key for :attr:`onsite_terms` or :attr:`coupling_terms`.
        plus_hc : bool
            If `True`, the hermitian conjugate of the terms is added automatically.
        """
        if self.explicit_plus_hc:
            if plus_hc:
                plus_hc = False  # explicitly add the h.c. later; don't do it here.
            else:
                strength /= 2  # avoid double-counting this term: add the h.c. explicitly later on
        # convert lattice to MPS index
        term = [(op, self.lat.lat2mps_idx(idx)) for op, idx in term]
        if category is None:
            category = "local " + " ".join([op for op, i in term])
        sites = self.lat.mps_sites()
        N = len(sites)
        if len(term) == 1:
            ot = self.onsite_terms.setdefault(category, OnsiteTerms(N))
            op, i = term[0]
            if sites[i].op_needs_JW(op):
                raise ValueError("can't add onsite operator which needs a Jordan-Wigner string!")
            ot.add_onsite_term(strength, i, op)
        elif len(term) == 2:
            ct = self.coupling_terms.setdefault(category, CouplingTerms(N))
            args = ct.coupling_term_handle_JW(strength, term, sites)
            ct.add_coupling_term(*args)
        elif len(term) > 2:
            # this case belongs into the MultiCouplingModel,
            # but then we would need to copy-paste the above parts...
            if not isinstance(self, MultiCouplingModel):
                raise ValueError("term has too many operators for CouplingModel, "
                                 "make it a MultiCouplingModel!")
            ct = self.coupling_terms.setdefault(category, MultiCouplingTerms(N))
            if not isinstance(ct, MultiCouplingTerms):
                # convert ct to MultiCouplingTerms
                self.coupling_terms[category] = new_ct = MultiCouplingTerms(self.lat.N_sites)
                new_ct += ct
                ct = new_ct
            args = ct.multi_coupling_term_handle_JW(strength, term, sites)
            ct.add_multi_coupling_term(*args)
        else:
            raise ValueError("empty term!")
        if plus_hc:
            hc_term = [(sites[i % N].get_hc_op_name(op), i) for op, i in reversed(term)]
            self.add_local_term(np.conj(strength), hc_term, category, plus_hc=False)

    def add_onsite(self, strength, u, opname, category=None, plus_hc=False):
        r"""Add onsite terms to :attr:`onsite_terms`.

        Adds :math:`\sum_{\vec{x}} strength[\vec{x}] * OP`` to the represented Hamiltonian,
        where the operator ``OP=lat.unit_cell[u].get_op(opname)``
        acts on the site given by a lattice index ``(x_0, ..., x_{dim-1}, u)``,

        The necessary terms are just added to :attr:`onsite_terms`; doesn't rebuild the MPO.

        Parameters
        ----------
        strength : scalar | array
            Prefactor of the onsite term. May vary spatially. If an array of smaller size
            is provided, it gets tiled to the required shape.
        u : int
            Picks a :class:`~tenpy.model.lattice.Site` ``lat.unit_cell[u]`` out of the unit cell.
        opname : str
            valid operator name of an onsite operator in ``lat.unit_cell[u]``.
        category : str
            Descriptive name used as key for :attr:`onsite_terms`. Defaults to `opname`.
        plus_hc : bool
            If `True`, the hermitian conjugate of the terms is added automatically.

        See also
        --------
        add_coupling : Add a terms acting on two sites.
        add_onsite_term : Add a single term without summing over :math:`vec{x}`.
        """
        strength = to_array(strength, self.lat.Ls)  # tile to lattice shape
        if not np.any(strength != 0.):
            return  # nothing to do: can even accept non-defined `opname`.
        if self.explicit_plus_hc:
            if plus_hc:
                plus_hc = False  # explicitly add the h.c. later; don't do it here.
            else:
                strength /= 2  # avoid double-counting this term: add the h.c. explicitly later on
        if not self.lat.unit_cell[u].valid_opname(opname):
            raise ValueError("unknown onsite operator {0!r} for u={1:d}\n"
                             "{2!r}".format(opname, u, self.lat.unit_cell[u]))
        if self.lat.unit_cell[u].op_needs_JW(opname):
            raise ValueError("can't add onsite operator which needs a Jordan-Wigner string!")
        if category is None:
            category = opname
        ot = self.onsite_terms.setdefault(category, OnsiteTerms(self.lat.N_sites))
        for i, i_lat in zip(*self.lat.mps_lat_idx_fix_u(u)):
            ot.add_onsite_term(strength[tuple(i_lat)], i, opname)
        if plus_hc:
            hc_op = self.lat.unit_cell[u].get_hc_op_name(opname)
            self.add_onsite(np.conj(strength), u, hc_op, category, plus_hc=False)

    def add_onsite_term(self, strength, i, op, category=None, plus_hc=False):
        """Add an onsite term on a given MPS site.

        Wrapper for ``self.onsite_terms[category].add_onsite_term(...)``.

        Parameters
        ----------
        strength : float
            The strength of the term.
        i : int
            The MPS index of the site on which the operator acts.
            We require ``0 <= i < L``.
        op : str
            Name of the involved operator.
        category : str
            Descriptive name used as key for :attr:`onsite_terms`. Defaults to `op`.
        plus_hc : bool
            If `True`, the hermitian conjugate of the term is added automatically.
        """
        if self.explicit_plus_hc:
            if plus_hc:
                plus_hc = False  # explicitly add the h.c. later; don't do it here.
            else:
                strength /= 2  # avoid double-counting this term: add the h.c. explicitly later on
        if category is None:
            category = op
        ot = self.onsite_terms.setdefault(category, OnsiteTerms(self.lat.N_sites))
        ot.add_onsite_term(strength, i, op)
        if plus_hc:
            site = self.lat.unit_cell[self.lat.order[i, -1]]
            hc_op = site.get_hc_op_name(opname)
            ot.add_onsite_term(np.conj(strength), i, hc_op)

    def all_onsite_terms(self):
        """Sum of all :attr:`onsite_terms`."""
        sites = self.lat.mps_sites()
        ot = OnsiteTerms(len(sites))
        for t in self.onsite_terms.values():
            ot += t
        return ot

    def add_coupling(self,
                     strength,
                     u1,
                     op1,
                     u2,
                     op2,
                     dx,
                     op_string=None,
                     str_on_first=True,
                     raise_op2_left=False,
                     category=None,
                     plus_hc=False):
        r"""Add twosite coupling terms to the Hamiltonian, summing over lattice sites.

        Represents couplings of the form
        :math:`\sum_{x_0, ..., x_{dim-1}} strength[shift(\vec{x})] * OP0 * OP1`, where
        ``OP0 := lat.unit_cell[u0].get_op(op0)`` acts on the site ``(x_0, ..., x_{dim-1}, u1)``,
        and ``OP1 := lat.unit_cell[u1].get_op(op1)`` acts on the site
        ``(x_0+dx[0], ..., x_{dim-1}+dx[dim-1], u1)``.
        Possible combinations ``x_0, ..., x_{dim-1}`` are determined from the boundary conditions
        in :meth:`~tenpy.models.lattice.Lattice.possible_couplings`.

        The coupling `strength` may vary spatially if the given `strength` is a numpy array.
        The correct shape of this array is the `coupling_shape` returned by
        :meth:`tenpy.models.lattice.possible_couplings` and depends on the boundary
        conditions. The ``shift(...)`` depends on `dx`,
        and is chosen such that the first entry ``strength[0, 0, ...]`` of `strength`
        is the prefactor for the first possible coupling
        fitting into the lattice if you imagine open boundary conditions.

        The necessary terms are just added to :attr:`coupling_terms`;
        this function does not rebuild the MPO.

        .. deprecated:: 0.4.0
            The arguments `str_on_first` and `raise_op2_left` will be removed in version 1.0.0.

        Parameters
        ----------
        strength : scalar | array
            Prefactor of the coupling. May vary spatially (see above). If an array of smaller size
            is provided, it gets tiled to the required shape.
        u1 : int
            Picks the site ``lat.unit_cell[u1]`` for OP1.
        op1 : str
            Valid operator name of an onsite operator in ``lat.unit_cell[u1]`` for OP1.
        u2 : int
            Picks the site ``lat.unit_cell[u2]`` for OP2.
        op2 : str
            Valid operator name of an onsite operator in ``lat.unit_cell[u2]`` for OP2.
        dx : iterable of int
            Translation vector (of the unit cell) between OP1 and OP2.
            For a 1D lattice, a single int is also fine.
        op_string : str | None
            Name of an operator to be used between the OP1 and OP2 sites.
            Typical use case is the phase for a Jordan-Wigner transformation.
            The operator should be defined on all sites in the unit cell.
            If ``None``, auto-determine whether a Jordan-Wigner string is needed, using
            :meth:`~tenpy.networks.site.Site.op_needs_JW`.
        str_on_first : bool
            Whether the provided `op_string` should also act on the first site.
            This option should be chosen as ``True`` for Jordan-Wigner strings.
            When handling Jordan-Wigner strings we need to extend the `op_string` to also act on
            the 'left', first site (in the sense of the MPS ordering of the sites given by the
            lattice). In this case, there is a well-defined ordering of the operators in the
            physical sense (i.e. which of `op1` or `op2` acts first on a given state).
            We follow the convention that `op2` acts first (in the physical sense),
            independent of the MPS ordering.
            Deprecated.
        raise_op2_left : bool
            Raise an error when `op2` appears left of `op1`
            (in the sense of the MPS ordering given by the lattice). Deprecated.
        category : str
            Descriptive name used as key for :attr:`coupling_terms`.
            Defaults to a string of the form ``"{op1}_i {op2}_j"``.
        plus_hc : bool
            If `True`, the hermitian conjugate of the terms is added automatically.

        Examples
        --------
        When initializing a model, you can add a term :math:`J \sum_{<i,j>} S^z_i S^z_j`
        on all nearest-neighbor bonds of the lattice like this:

        >>> J = 1.  # the strength
        >>> for u1, u2, dx in self.lat.pairs['nearest_neighbors']:
        ...     self.add_coupling(J, u1, 'Sz', u2, 'Sz', dx)

        The strength can be an array, which gets tiled to the correct shape.
        For example, in a 1D :class:`~tenpy.models.lattice.Chain` with an even number of sites and
        periodic (or infinite) boundary conditions, you can add alternating strong and weak
        couplings with a line like::

        >>> self.add_coupling([1.5, 1.], 0, 'Sz', 0, 'Sz', dx)

        Make sure to use the `plus_hc` argument if necessary, e.g. for hoppings:

        >>> for u1, u2, dx in self.lat.pairs['nearest_neighbors']:
        ...     self.add_coupling(t, u1, 'Cd', u2, 'C', dx, plus_hc=True)

        Alternatively, you can add the hermitian conjugate terms explictly. The correct way is to
        complex conjugate the strength, take the hermitian conjugate of the operators and swap the
        order (including a swap `u1` <-> `u2`), and use the opposite direction ``-dx``, i.e.
        the `h.c.` of ``add_coupling(t, u1, 'A', u2, 'B', dx)` is
        ``add_coupling(np.conj(t), u2, hc('B'), u1, hc('A'), -dx)``, where `hc` takes the hermitian
        conjugate of the operator names, see :meth:`~tenpy.networks.site.Site.get_hc_op_name`.
        For spin-less fermions (:class:`~tenpy.networks.site.FermionSite`), this would be

        >>> t = 1.  # hopping strength
        >>> for u1, u2, dx in self.lat.pairs['nearest_neighbors']:
        ...     self.add_coupling(t, u1, 'Cd', u2, 'C', dx)
        ...     self.add_coupling(np.conj(t), u2, 'Cd', u1, 'C', -dx)  # h.c.

        With spin-full fermions (:class:`~tenpy.networks.site.SpinHalfFermions`), it could be:

        >>> for u1, u2, dx in self.lat.pairs['nearest_neighbors']:
        ...     self.add_coupling(t, u1, 'Cdu', u2, 'Cd', dx)  # Cdagger_up C_down
        ...     self.add_coupling(np.conj(t), u2, 'Cdd', u1, 'Cu', -dx)  # h.c. Cdagger_down C_up

        Note that the Jordan-Wigner strings for the fermions are added automatically!

        See also
        --------
        add_onsite : Add terms acting on one site only.
        MultiCouplingModel.add_multi_coupling_term : for terms on more than two sites.
        add_coupling_term : Add a single term without summing over :math:`vec{x}`.
        """
        dx = np.array(dx, np.intp).reshape([self.lat.dim])
        if not np.any(np.asarray(strength) != 0.):
            return  # nothing to do: can even accept non-defined onsite operators
        for op, u in [(op1, u1), (op2, u2)]:
            if not self.lat.unit_cell[u].valid_opname(op):
                raise ValueError(("unknown onsite operator {0!r} for u={1:d}\n"
                                  "{2!r}").format(op, u, self.lat.unit_cell[u]))
        site1 = self.lat.unit_cell[u1]
        site2 = self.lat.unit_cell[u2]
        if op_string is None:
            need_JW1 = site1.op_needs_JW(op1)
            need_JW2 = site2.op_needs_JW(op2)
            if need_JW1 and need_JW2:
                op_string = 'JW'
                str_on_first = True
            elif need_JW1 or need_JW2:
                raise ValueError("Only one of the operators needs a Jordan-Wigner string?!")
            else:
                op_string = 'Id'
        for u in range(len(self.lat.unit_cell)):
            if not self.lat.unit_cell[u].valid_opname(op_string):
                raise ValueError("unknown onsite operator {0!r} for u={1:d}\n"
                                 "{2!r}".format(op_string, u, self.lat.unit_cell[u]))
        if op_string == "JW" and not str_on_first:
            raise ValueError("Jordan Wigner string without `str_on_first`")
        if np.all(dx == 0) and u1 == u2:
            raise ValueError("Coupling shouldn't be onsite!")
        mps_i, mps_j, lat_indices, strength_shape = self.lat.possible_couplings(u1, u2, dx)
        strength = to_array(strength, strength_shape)  # tile to correct shape
        if self.explicit_plus_hc:
            if plus_hc:
                plus_hc = False  # explicitly add the h.c. later; don't do it here.
            else:
                strength /= 2  # avoid double-counting this term: add the h.c. explicitly later on
        if category is None:
            category = "{op1}_i {op2}_j".format(op1=op1, op2=op2)
        ct = self.coupling_terms.setdefault(category, CouplingTerms(self.lat.N_sites))
        # loop to perform the sum over {x_0, x_1, ...}
        for i, j, lat_idx in zip(mps_i, mps_j, lat_indices):
            current_strength = strength[tuple(lat_idx)]
            if current_strength == 0.:
                continue
            # the following is roughly equivalent to
            # CouplingTerms.coupling_term_handle_JW, but also swaps i <-> j if necessary
            # and allows `str_on_first` being set explicitly
            o1, o2 = op1, op2
            site_i = site1
            if j < i:  # ensure i <= j
                # swap operators
                i, j = j, i
                if op_string == 'JW':
                    current_strength = -current_strength  # swap sign
                if raise_op2_left:
                    raise ValueError("Op2 is left")
                o1, o2 = op2, op1
                site_i = site2
            # now we have always i < j and 0 <= i < N_sites
            # j >= N_sites indicates couplings between unit_cells of the infinite MPS.
            # o1 is the "left" operator; o2 is the "right" operator
            if str_on_first and op_string != 'Id':
                o1 = site_i.multiply_op_names([o1, op_string])
            ct.add_coupling_term(current_strength, i, j, o1, o2, op_string)

        if plus_hc:
            hc_op1 = site1.get_hc_op_name(op1)
            hc_op2 = site2.get_hc_op_name(op2)
            hc_opstr = site2.get_hc_op_name(op_string)
            self.add_coupling(np.conj(strength), u2, hc_op2, u1, hc_op1, -dx,
                              hc_opstr, str_on_first, raise_op2_left,
                              category, plus_hc=False)  # yapf: disable
        # done

    def add_coupling_term(self,
                          strength,
                          i,
                          j,
                          op_i,
                          op_j,
                          op_string='Id',
                          category=None,
                          plus_hc=False):
        """Add a two-site coupling term on given MPS sites.

        Wrapper for ``self.coupling_terms[category].add_coupling_term(...)``.

        .. warning ::
            This function does not handle Jordan-Wigner strings!
            You might want to use :meth:`add_local_term` instead.

        Parameters
        ----------
        strength : float
            The strength of the coupling term.
        i, j : int
            The MPS indices of the two sites on which the operator acts.
            We require ``0 <= i < N_sites``  and ``i < j``, i.e., `op_i` acts "left" of `op_j`.
            If j >= N_sites, it indicates couplings between unit cells of an infinite MPS.
        op1, op2 : str
            Names of the involved operators.
        op_string : str
            The operator to be inserted between `i` and `j`.
        category : str
            Descriptive name used as key for :attr:`coupling_terms`.
            Defaults to a string of the form ``"{op1}_i {op2}_j"``.
        plus_hc : bool
            If `True`, the hermitian conjugate of the term is added automatically.
        """
        if self.explicit_plus_hc:
            if plus_hc:
                plus_hc = False  # explicitly add the h.c. later; don't do it here.
            else:
                strength /= 2  # avoid double-counting this term: add the h.c. explicitly later on
        if category is None:
            category = "{op_i}_i {op_j}_j".format(op_i=op_i, op_j=op_j)
        ct = self.coupling_terms.setdefault(category, CouplingTerms(self.lat.N_sites))
        ct.add_coupling_term(strength, i, j, op_i, op_j, op_string)
        if plus_hc:
            site_i = self.lat.unit_cell[self.lat.order[i, -1]]
            site_j = self.lat.unit_cell[self.lat.order[j % self.lat.N_sites, -1]]
            hc_op_i = site_i.get_hc_op_name(op_i)
            # NB: op_string should be defined on all sites in the unit cell...
            hc_op_string = site_i.get_hc_op_name(op_string)
            hc_op_j = site_j.get_hc_op_name(op_j)
            ct.add_coupling_term(np.conj(strength), i, j, hc_op_i, hc_op_j, hc_op_string)

    def all_coupling_terms(self):
        """Sum of all :attr:`coupling_terms`."""
        sites = self.lat.mps_sites()
        if any([isinstance(ct, MultiCouplingTerms) for ct in self.coupling_terms.values()]):
            ct = MultiCouplingTerms(len(sites))
        else:
            ct = CouplingTerms(len(sites))
        for t in self.coupling_terms.values():
            ct += t
        return ct

    def calc_H_onsite(self, tol_zero=1.e-15):
        """Calculate `H_onsite` from `self.onsite_terms`.

        .. deprecated:: 0.4.0
            This function will be removed in 1.0.0.
            Replace calls to this function by
            ``self.all_onsite_terms().remove_zeros(tol_zero).to_Arrays(self.lat.mps_sites())``.
            You might also want to take :attr:`explicit_plus_hc` into account.

        Parameters
        ----------
        tol_zero : float
            prefactors with ``abs(strength) < tol_zero`` are considered to be zero.

        Returns
        -------
        H_onsite : list of npc.Array
        onsite terms of the Hamiltonian. If :attr:`explicit_plus_hc` is True,
            Hermitian conjugates of the onsite terms will be included.
        """
        warnings.warn("Deprecated `calc_H_onsite` in CouplingModel", FutureWarning, stacklevel=2)
        ot = self.all_onsite_terms()
        ot.remove_zeros(tol_zero)
        ot_arrays = ot.to_Arrays(self.lat.mps_sites())
        if self.explicit_plus_hc:
            for i, op in enumerate(ot_arrays):
                if op is not None:
                    ot_arrays[i] = op + op.conj().itranspose(op.get_leg_labels())
        return ot_arrays

    def calc_H_bond(self, tol_zero=1.e-15):
        """calculate `H_bond` from :attr:`coupling_terms` and :attr:`onsite_terms`.

        Parameters
        ----------
        tol_zero : float
            prefactors with ``abs(strength) < tol_zero`` are considered to be zero.

        Returns
        -------
        H_bond : list of :class:`~tenpy.linalg.np_conserved.Array`
            Bond terms as required by the constructor of :class:`NearestNeighborModel`.
            Legs are ``['p0', 'p0*', 'p1', 'p1*']``

        Raises
        ------
        ValueError : if the Hamiltonian contains longer-range terms.
        """
        sites = self.lat.mps_sites()
        finite = (self.lat.bc_MPS != 'infinite')

        ct = self.all_coupling_terms()
        ct.remove_zeros(tol_zero)
        H_bond = ct.to_nn_bond_Arrays(sites)

        ot = self.all_onsite_terms()
        ot.remove_zeros(tol_zero)
        ot.add_to_nn_bond_Arrays(H_bond, sites, finite, distribute=(0.5, 0.5))

        if finite:
            assert H_bond[0] is None
        if self.explicit_plus_hc:
            # self representes the terms of `ct` and `ot` + their hermitian conjugates
            # so we need to explicitly add the hermitian conjugate terms
            for i, Hb in enumerate(H_bond):
                if Hb is not None:
                    H_bond[i] = Hb + Hb.conj().itranspose(Hb.get_leg_labels())
        return H_bond

    def calc_H_MPO(self, tol_zero=1.e-15):
        """Calculate MPO representation of the Hamiltonian.

        Uses :attr:`onsite_terms` and :attr:`coupling_terms` to build an MPO graph
        (and then an MPO).

        Parameters
        ----------
        tol_zero : float
            Prefactors with ``abs(strength) < tol_zero`` are considered to be zero.

        Returns
        -------
        H_MPO : :class:`~tenpy.networks.mpo.MPO`
            MPO representation of the Hamiltonian.
        """
        ot = self.all_onsite_terms()
        ot.remove_zeros(tol_zero)
        ct = self.all_coupling_terms()
        ct.remove_zeros(tol_zero)

        H_MPO_graph = mpo.MPOGraph.from_terms(ot, ct, self.lat.mps_sites(), self.lat.bc_MPS)
        H_MPO = H_MPO_graph.build_MPO()
        H_MPO.max_range = ct.max_range()
        H_MPO.explicit_plus_hc = self.explicit_plus_hc
        return H_MPO

    def coupling_strength_add_ext_flux(self, strength, dx, phase):
        """Add an external flux to the coupling strength.

        When performing DMRG on a "cylinder" geometry, it might be useful to put an "external flux"
        through the cylinder. This means that a particle hopping around the cylinder should
        pick up a phase given by the external flux [Resta1997]_.
        This is also called "twisted boundary conditions" in literature.
        This function adds a complex phase to the `strength` array on some bonds, such that
        particles hopping in positive direction around the cylinder pick up `exp(+i phase)`.

        .. warning ::
            For the sign of `phase` it is important that you consistently use the creation
            operator as `op1` and the annihilation operator as `op2` in :meth:`add_coupling`.

        Parameters
        ----------
        strength : scalar | array
            The strength to be used in :meth:`add_coupling`, when no external flux would be
            present.
        dx : iterable of int
            Translation vector (of the unit cell) between `op1` and `op2` in :meth:`add_coupling`.
        phase : iterable of float
            The phase of the external flux for hopping in each direction of the lattice.
            E.g., if you want flux through the cylinder on which you have an infinite MPS,
            you should give ``phase=[0, phi]`` souch that particles pick up a phase `phi` when
            hopping around the cylinder.

        Returns
        -------
        strength : complex array
            The strength array to be used as `strength` in :meth:`add_coupling`
            with the given `dx`.

        Examples
        --------
        Let's say you have an infinite MPS on a cylinder, and want to add nearest-neighbor
        hopping of fermions with the :class:`~tenpy.networks.site.FermionSite`.
        The cylinder axis is the `x`-direction of the lattice, so to put a flux through the
        cylinder, you want particles hopping *around* the cylinder to pick up a phase `phi`
        given by the external flux.

        >>> strength = 1. # hopping strength without external flux
        >>> phi = np.pi/4 # determines the external flux strength
        >>> strength_with_flux = self.coupling_strength_add_ext_flux(strength, dx, [0, phi])
        >>> for u1, u2, dx in self.lat.pairs['nearest_neighbors']:
        ...     self.add_coupling(strength_with_flux, u1, 'Cd', u2, 'C', dx)
        ...     self.add_coupling(np.conj(strength_with_flux), u2, 'Cd', u1, 'C', -dx)
        """
        c_shape = self.lat.coupling_shape(dx)[0]
        strength = to_array(strength, c_shape)
        # make strenght complex
        complex_dtype = np.find_common_type([strength.dtype], [np.dtype(np.complex)])
        strength = np.asarray(strength, complex_dtype)
        for ax in range(self.lat.dim):
            if self.lat.bc[ax]:  # open boundary conditions
                if phase[ax]:
                    raise ValueError("Nonzero phase for external flux along non-periodic b.c.")
                continue
            if abs(dx[ax]) == 0:
                continue  # nothing to do
            slices = [slice(None) for _ in range(self.lat.dim)]
            slices[ax] = slice(-abs(dx[ax]), None)
            # the last ``abs(dx[ax])`` entries in the axis `ax` correspond to hopping
            # accross the periodic b.c.
            slices = tuple(slices)
            if dx[ax] > 0:
                strength[slices] *= np.exp(-1.j * phase[ax])  # hopping in *negative* y-direction
            else:
                strength[slices] *= np.exp(1.j * phase[ax])  # hopping in *positive* y-direction
        return strength


class MultiCouplingModel(CouplingModel):
    """Generalizes :class:`CouplingModel` to allow couplings involving more than two sites.

    The corresponding couplings can be added with :meth:`add_multi_coupling` and
    :meth:`add_multi_coupling_term` and are saved in :attr:`coupling_terms`, which can now contain
    instances of :class:`~tenpy.networks.terms.MultiCouplingTerms`.
    """
    def add_multi_coupling(self,
                           strength,
                           ops,
                           _deprecate_1=_DEPRECATED_ARG_NOT_SET,
                           _deprecate_2=_DEPRECATED_ARG_NOT_SET,
                           op_string=None,
                           category=None,
                           plus_hc=False):
        r"""Add multi-site coupling terms to the Hamiltonian, summing over lattice sites.

        Represents couplings of the form
        :math:`sum_{\vec{x}} strength[shift(\vec{x})] * OP_0 * OP_1 * ... * OP_{M-1}`,
        involving `M` operators.
        Here, :math:`OP_m` stands for the operator defined by the `m`-th tuple
        ``(opname, dx, u)`` given in the argument `ops`, which determines the position
        :math:`\vec{x} + \vec{dx}` and unit-cell index `u` of the site it acts on;
        the actual operator is given by `self.lat.unit_cell[u].get_op(opname)`.

        The coupling `strength` may vary spatially if the given `strength` is a numpy array.
        The correct shape of this array is the `coupling_shape` returned by
        :meth:`tenpy.models.lattice.possible_multi_couplings` and depends on the boundary
        conditions. The ``shift(...)`` depends on the `dx` entries of `ops`
        and is chosen such that the first entry ``strength[0, 0, ...]`` of `strength`
        is the prefactor for the first possible coupling
        fitting into the lattice if you imagine open boundary conditions.

        The necessary terms are just added to :attr:`coupling_terms`;
        this function does not rebuild the MPO.

        .. deprecated:: 0.6.0
            We switched from the three arguments `u0`, `op0` and `other_op` with
            ``other_ops=[(u1, op1, dx1), (op2, u2, dx2), ...]``
            to a single, equivalent argment `ops` which should now read
            ``ops=[(op0, dx0, u0), (op1, dx1, u1), (op2, dx2, u2), ...]``, where
            ``dx0 = [0]*self.lat.dim``. Note the changed order inside the tuples!

        Parameters
        ----------
        strength : scalar | array
            Prefactor of the coupling. May vary spatially, and is tiled to the required shape.
        ops : list of ``(opname, dx, u)``
            Each tuple determines one operator of the coupling, see the description above.
            `opname` (str) is the name of the operator,
            `dx` (list of length `lat.dim`) is a translation vector, and
            `u` (int) is the index of `lat.unit_cell` on which the operator acts.
            The first entry of `ops` corresponds to :math:`OP_0` and acts last in the physical
            sense.
        op_string : str | None
            If a string is given, we use this as the name of an operator to be used inbetween
            the operators, *excluding* the sites on which any operators act.
            This operator should be defined on all sites in the unit cell.

            If ``None``, auto-determine whether a Jordan-Wigner string is needed
            (using :meth:`~tenpy.networks.site.Site.op_needs_JW`) for each of the segments
            inbetween the operators and also on the sites of the left operators.

            .. warning :
                ``None`` figures out for each segment between the operators, whether a
                Jordan-Wigner string is needed.
                This is different from a plain ``'JW'``, which just applies a string on
                *each* segment and gives wrong results e.g. for Cd-C-Cd-C terms!

        category : str
            Descriptive name used as key for :attr:`coupling_terms`.
            Defaults to a string of the form ``"{op0}_i {other_ops[0]}_j {other_ops[1]}_k ..."``.
        plus_hc : bool
            If `True`, the hermitian conjugate of the terms is added automatically.

        Examples
        --------
        A call to :meth:`add_coupling` with arguments
        ``add_coupling(strength, u1, 'A', u2, 'B', dx)`` is equivalent to the following::

        >>> dx_0 = [0] * self.lat.dim  # = [0] for a 1D lattice, [0, 0] in 2D
        >>> self.add_coupling(strength, [('A', dx_0, u1), ('B', dx, u2)])

        To explicitly add the hermitian conjugate, you need to take the complex conjugate of the
        `strength`, reverse the order of the operators and take the hermitian conjugates of the
        individual operator names:

        >>> self.add_coupling(np.conj(strength), [(hc('B'), dx, u2), (hc('A'), dx_0, u1)])  # h.c.

        See also
        --------
        add_onsite : Add terms acting on one site only.
        add_coupling : Add terms acting on two sites.
        add_multi_coupling_term : Add a single term, not summing over the possible :math:`\vec{x}`.
        """
        if _deprecate_1 is not _DEPRECATED_ARG_NOT_SET or \
                _deprecate_2 is not _DEPRECATED_ARG_NOT_SET:
            msg = ("Deprecated arguments of MultiCouplingModel.add_multi_coupling:\n"
                   "switch to using a single argument \n"
                   "     ops=[(op0, [0]*self.lat.dim, u0), (op1, dx1, u1), (op2, dx2, u2), ...]\n"
                   "instead of the three arguments \n"
                   "     u0\n"
                   "     op0\n"
                   "     other_ops=[(u1, op1, dx1), (op2, u2, dx2), ...]\n"
                   "Note the reordering ``(u, op, dx) -> (op, dx, u)`` in the tuples!")
            warnings.warn(msg, FutureWarning, stacklevel=2)
            u0 = ops
            op0 = _deprecate_1
            dx0 = [0] * self.lat.dim
            other_ops = _deprecate_2
            # new argument:
            ops = [(op0, dx0, u0)] + [(op, dx, u) for (u, op, dx) in other_ops]
        # split `ops` into separate groups
        all_ops = [t[0] for t in ops]
        all_us = np.array([t[2] for t in ops], np.intp)
        all_dxs = np.array([t[1] for t in ops], np.intp).reshape([len(ops), self.lat.dim])
        if not np.any(np.asarray(strength) != 0.):
            return  # nothing to do: can even accept non-defined onsite operators
        need_JW = np.array([self.lat.unit_cell[u].op_needs_JW(op) for op, _, u in ops],
                           dtype=np.bool_)
        if not np.sum(need_JW) % 2 == 0:
            raise ValueError("Invalid coupling: odd number of operators which need 'JW' string")
        if op_string is None and not any(need_JW):
            op_string = 'Id'
        for op, _, u in ops:
            if not self.lat.unit_cell[u].valid_opname(op):
                raise ValueError("unknown onsite operator {0!r} for u={1:d}\n"
                                 "{2!r}".format(op, u, self.lat.unit_cell[u]))
        if op_string is not None:
            for u in range(len(self.lat.unit_cell)):
                if not self.lat.unit_cell[u].valid_opname(op_string):
                    raise ValueError("unknown onsite operator {0!r} for u={1:d}\n"
                                     "{2!r}".format(op_string, u, self.lat.unit_cell[u]))
        if np.all(all_dxs == all_dxs[0, :]) and np.all(all_us[0] == all_us):
            # note: we DO allow couplings with some onsite terms, but not all of them
            raise ValueError("Coupling shouldn't be purely onsite!")

        # prepare: figure out the necessary mps indices
        mps_ijkl, lat_indices, strength_shape = self.lat.possible_multi_couplings(ops)
        strength = to_array(strength, strength_shape)  # tile to correct shape
        if self.explicit_plus_hc:
            if plus_hc:
                plus_hc = False  # explicitly add the h.c. later; don't do it here.
            else:
                strength /= 2  # avoid double-counting this term: add the h.c. explicitly later on
        if category is None:
            category = " ".join(
                ["{op}_{i}".format(op=op, i=chr(ord('i') + m)) for m, op in enumerate(all_ops)])
        ct = self.coupling_terms.setdefault(category, MultiCouplingTerms(self.lat.N_sites))
        if not isinstance(ct, MultiCouplingTerms):
            # convert ct to MultiCouplingTerms
            self.coupling_terms[category] = new_ct = MultiCouplingTerms(self.lat.N_sites)
            new_ct += ct
            ct = new_ct
        N_sites = self.lat.N_sites
        sites = self.lat.mps_sites()
        # loop to perform the sum over {x_0, x_1, ...}
        for ijkl, i_lat in zip(mps_ijkl, lat_indices):
            current_strength = strength[tuple(i_lat)]
            if current_strength == 0.:
                continue
            term = list(zip(all_ops, ijkl))
            term, sign = order_combine_term(term, sites)
            args = ct.multi_coupling_term_handle_JW(current_strength * sign, term, sites,
                                                    op_string)
            ct.add_multi_coupling_term(*args)

        # add h.c. term
        if plus_hc:
            hc_ops = [(self.lat.unit_cell[u].get_hc_op_name(opname), dx, u)
                      for (opname, dx, u) in reversed(ops)]
            self.add_multi_coupling(np.conj(strength), hc_ops, category=category, plus_hc=False)
        # done

    def add_multi_coupling_term(self,
                                strength,
                                ijkl,
                                ops_ijkl,
                                op_string,
                                category=None,
                                plus_hc=False):
        """Add a general M-site coupling term on given MPS sites.

        Wrapper for ``self.coupling_terms[category].add_multi_coupling_term(...)``.

        .. warning ::
            This function does not handle Jordan-Wigner strings!
            You might want to use :meth:`add_local_term` instead.

        Parameters
        ----------
        strength : float
            The strength of the coupling term.
        ijkl : list of int
            The MPS indices of the sites on which the operators acts. With `i, j, k, ... = ijkl`,
            we require that they are ordered ascending, ``i < j < k < ...`` and
            that ``0 <= i < N_sites``.
            Inidces >= N_sites indicate couplings between different unit cells of an infinite MPS.
        ops_ijkl : list of str
            Names of the involved operators on sites `i, j, k, ...`.
        op_string : list of str
            Names of the operator to be inserted between the operators,
            e.g., op_string[0] is inserted between `i` and `j`.
        category : str
            Descriptive name used as key for :attr:`coupling_terms`.
            Defaults to a string of the form ``"{op0}_i {op1}_j {op2}_k ..."``.
        plus_hc : bool
            If `True`, the hermitian conjugate of the term is added automatically.
        """
        if self.explicit_plus_hc:
            if plus_hc:
                plus_hc = False  # explicitly add the h.c. later; don't do it here.
            else:
                strength /= 2  # avoid double-counting this term: add the h.c. explicitly later on
        if category is None:
            category = " ".join(
                ["{op}_{i}".format(op=op, i=chr(ord('i') + m)) for m, op in enumerate(ops_ijkl)])
        ct = self.coupling_terms.get(category, None)
        if ct is None:
            self.coupling_terms[category] = ct = MultiCouplingTerms(self.lat.N_sites)
        elif not isinstance(ct, MultiCouplingTerms):
            self.coupling_terms[category] = new_ct = MultiCouplingTerms(self.lat.N_sites)
            new_ct += ct
            ct = new_ct
        ct.add_multi_coupling_term(strength, ijkl, ops_ijkl, op_string)
        if plus_hc:
            sites_ijkl = [
                self.lat.unit_cell[self.lat.order[i % self.lat.N_sites, -1]] for i in ijkl
            ]
            hc_ops = [site.get_hc_op_name(op) for site, op in zip(sites_ijkl, ops_ijkl)]
            # NB: op_string should be defined on all sites in the unit cell...
            hc_op_string = [site.get_hc_op_name(op) for site, op in zip(sites_ijkl, op_string)]
            ct.add_multi_coupling_term(np.conj(strength), ijkl, ops_ijkl, op_string)


class CouplingMPOModel(CouplingModel, MPOModel):
    """Combination of the :class:`CouplingModel` and :class:`MPOModel`.

    This class provides the interface for most of the model classes in `tenpy`.
    Examples based on this class are given in :mod:`~tenpy.models.xxz_chain`
    and :mod:`~tenpy.models.tf_ising`.

    The ``__init__`` of this function performs the standard initialization explained
    in :doc:`/intro/model`, by calling the methods :meth:`init_lattice` (step 1-4)
    to initialize a lattice (which in turn calls :meth:`init_sites`) and
    :meth:`init_terms`. The latter should be overwritten by subclasses to add the
    desired terms.

    As shown in :mod:`~tenpy.models.tf_ising`, you can get a 1D version suitable
    for TEBD from a general-lattice model by subclassing it once more, only
    redefining the ``__init__`` as follows::

        def __init__(self, model_params):
            CouplingMPOModel.__init__(self, model_params)


    Parameters
    ----------
    model_params : dict
        A dictionary with all the model parameters.
        These parameters are converted to a (dict-like) :class:`~tenpy.tools.params.Config`,
        and then set as :attr:`options` and given to the different ``init_...()`` methods.

    Options
    -------
    .. cfg:config :: CouplingMPOModel

        sort_mpo_legs : bool = False
            Whether the virtual legs of the MPO should be sorted by charges,
            see :meth:`~tenpy.networks.mpo.MPO.sort_legcharges`.
        explicit_plus_hc : bool
            Whether the Hermitian conjugate of the MPO is computed at runtime,
            rather than saved in the MPO.

    Attributes
    ----------
    name : str
        The (class-) name of the model, e.g. ``"XXZChain" or ``"SpinModel"``.
    options: :class:`~tenpy.tools.params.Config`
        Optional parameters.
    verbose : int
        Level of verbosity (i.e. how much status information to print); higher=more output.
    """
    def __init__(self, model_params):
        if getattr(self, "_called_CouplingMPOModel_init", False):
            # If we ignore this, the same terms get added to self multiple times.
            # In the best case, this would just rescale the energy;
            # in the worst case we get the wrong Hamiltonian.
            raise ValueError("Called CouplingMPOModel.__init__(...) multiple times.")
            # To fix this problem, follow the instructions for subclassing in :doc:`/intro/model`.
        self.name = self.__class__.__name__
        self.options = model_params = asConfig(model_params, self.name)
        self._called_CouplingMPOModel_init = True
        self.verbose = model_params.get('verbose', 1)
        explicit_plus_hc = model_params.get('explicit_plus_hc', False)
        # 1-4) iniitalize lattice
        lat = self.init_lattice(model_params)
        # 5) initialize CouplingModel
        CouplingModel.__init__(self, lat, explicit_plus_hc=explicit_plus_hc)
        # 6) add terms of the Hamiltonian
        self.init_terms(model_params)
        # 7) initialize H_MPO
        H_MPO = self.calc_H_MPO()
        if model_params.get('sort_mpo_legs', False):
            H_MPO.sort_legcharges()
        MPOModel.__init__(self, lat, H_MPO)
        if isinstance(self, NearestNeighborModel):
            # 8) initialize H_bonds
            NearestNeighborModel.__init__(self, lat, self.calc_H_bond())
        # checks for misspelled parameters
        model_params.warn_unused()

    def init_lattice(self, model_params):
        """Initialize a lattice for the given model parameters.

        This function reads out the model parameter `lattice`.
        This can be a full :class:`~tenpy.models.lattice.Lattice` instance,
        in which case it is just returned without further action.
        Alternatively, the `lattice` parameter can be a string giving the name
        of one of the predefined lattices, which then gets initialized.
        Depending on the dimensionality of the lattice, this requires different model parameters.

        Parameters
        ----------
        model_params : dict
            The model parameters given to ``__init__``.

        Returns
        -------
        lat : :class:`~tenpy.models.lattice.Lattice`
            An initialized lattice.

        Options
        -------
        .. cfg:configoptions :: CouplingMPOModel

            lattice : str | Lattice
                The name of a lattice pre-defined in TeNPy to be initialized.
                Alternatively, a (possibly self-defined) Lattice instance.
                In the latter case, no further parameters are read out.
            bc_MPS : str
                Boundary conditions for the MPS.
            order : str
                The order of sites within the lattice for non-trivial lattices,
                e.g, ``'default', 'snake'``, see :meth:`~tenpy.models.lattice.Lattice.ordering`.
                Only used if `lattice` is a string.
            L : int
                The length in x-direction; only read out for 1D lattices.
                For an infinite system the length of the unit cell.
            Lx, Ly : int
                The length in x- and y-direction; only read out for 2D lattices.
                For ``"infinite"`` `bc_MPS`, the system is infinite in x-direction and
                `Lx` is the number of "rings" in the infinite MPS unit cell,
                while `Ly` gives the circumference around the cylinder or width of th the rung
                for a ladder (depending on `bc_y`).
            bc_y : str
               ``"cylinder" | "ladder"``; only read out for 2D lattices.
               The boundary conditions in y-direction.
            bc_x : str
                ``"open" | "periodic"``.
                Can be used to force "periodic" boundaries for the lattice,
                i.e., for the couplings in the Hamiltonian, even if the MPS is finite.
                Defaults to ``"open"`` for ``bc_MPS="finite"`` and
                ``"periodic"`` for ``bc_MPS="infinite``.
                If you are not aware of the consequences, you should probably
                *not* use "periodic" boundary conditions.
                (The MPS is still "open", so this will introduce long-range
                couplings between the first and last sites of the MPS!)

        """
        lat = model_params.get('lattice', "Chain")
        if isinstance(lat, str):
            LatticeClass = get_lattice(lattice_name=lat)
            bc_MPS = model_params.get('bc_MPS', 'finite')
            order = model_params.get('order', 'default')
            sites = self.init_sites(model_params)
            bc_x = 'periodic' if bc_MPS == 'infinite' else 'open'
            bc_x = model_params.get('bc_x', bc_x)
            if bc_MPS == 'infinite' and bc_x == 'open':
                raise ValueError("You need to use 'periodic' `bc_x` for infinite systems!")
            if LatticeClass.dim == 1:  # 1D lattice
                L = model_params.get('L', 2)
                # 4) lattice
                lat = LatticeClass(L, sites, bc=bc_x, bc_MPS=bc_MPS)
            elif LatticeClass.dim == 2:  # 2D lattice
                Lx = model_params.get('Lx', 1)
                Ly = model_params.get('Ly', 4)
                bc_y = model_params.get('bc_y', 'cylinder')
                assert bc_y in ['cylinder', 'ladder']
                bc_y = 'periodic' if bc_y == 'cylinder' else 'open'
                lat = LatticeClass(Lx, Ly, sites, order=order, bc=[bc_x, bc_y], bc_MPS=bc_MPS)
            else:
                raise ValueError("Can't auto-determine parameters for the lattice. "
                                 "Overwrite the `init_lattice` in your model!")
            # now, `lat` is an instance of the LatticeClass called `lattice_name`.
        # else: a lattice was already provided
        assert isinstance(lat, Lattice)
        return lat

    def init_sites(self, model_params):
        """Define the local Hilbert space and operators; needs to be implemented in subclasses.

        This function gets called by :meth:`init_lattice` to get the
        :class:`~tenpy.networks.site.Site` for the lattice unit cell.

        .. note ::
            Initializing the sites requires to define the conserved quantum numbers.
            All pre-defined sites accept ``conserve=None`` to disable using quantum numbers.
            Many models in TeNPy read out the `conserve` model parameter, which can be set
            to ``"best"`` to indicate the optimal parameters.

        Parameters
        ----------
        model_params : dict
            The model parameters given to ``__init__``.

        Returns
        -------
        sites : (tuple of) :class:`~tenpy.networks.site.Site`
            The local sites of the lattice, defining the local basis states and operators.
        """
        raise NotImplementedError("Subclasses should implement `init_sites`")
        # or at least redefine the lattice

    def init_terms(self, model_params):
        """Add the onsite and coupling terms to the model; subclasses should implement this."""
        pass  # Do nothing. This allows to super().init_terms(model_params) in subclasses.
