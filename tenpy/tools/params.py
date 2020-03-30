"""Tools to handle paramters for algorithms.

See the doc-string of :func:`get_parameter` for details.
"""
# Copyright 2018-2020 TeNPy Developers, GNU GPLv3

import warnings
import yaml
import numpy as np
from collections.abc import MutableMapping

from .hdf5_io import Hdf5Exportable

__all__ = ["Parameters", "get_parameter", "unused_parameters"]


class Parameters(MutableMapping, Hdf5Exportable):
    """Wrapper class for parameter dictionaries.
    
    Attributes
    ----------
    documentation : dict
        Contains type and general information ror parameters
    name : str
        Name oof the dictionary, for output statements. For example, when using
        a `Parameters` class for DMRG parameters, `name='DMRG'`
    params : dict
        Dictionary containing the actual parameters
    unused : set
        Keeps track of any parameters not yet used.
    verbose : int
        Verbosity level for output statements.
    """
    
    def __init__(self, params, name):
        self.params = params
        self.name = name
        self.verbose = params.get('verbose', 0)
        self.unused = set(params.keys())
        self.documentation = {}

    def __getitem__(self, key):
        self.print_if_verbose(key, "Reading")
        self.unused.discard(key)
        return self.params[key]

    def __setitem__(self, key, value):
        self.print_if_verbose(key, "Setting")
        self.params[key] = value

    def __delitem__(self, key):
        self.print_if_verbose(key, "Deleting")
        self.unused.discard(key)
        del self.params[key]

    def __iter__(self):
        return iter(self.params)

    def __len__(self):
        return len(self.params)

    def __str__(self):
        return repr(self)  # TODO This is not what we want 

    def __repr__(self):
        return "<Parameters, {0!s} parameters>".format(len(self))

    def __del__(self):
        unused = self.unused
        if len(unused) > 0:
            if len(unused) > 1:
                msg = "unused parameters for {name!s}:\n{keys!s}"
            else:
                msg = "unused parameter {keys!s} for {name!s}\n"
            warnings.warn(msg.format(keys=sorted(unused), name=self.name))
        return unused

    def get(self, key, default):
        """Find the value of `key`. If none is set, return `default` and set 
        the value of `key` to `default` internally.
        
        Parameters
        ----------
        key : str
            Key name for the parameter being read out.
        default : any type
            Default value for the parameter
        
        Returns
        -------
        val : any type
            The value for `key` if it existed, `default` otherwise.
        """
        val = self.params.setdefault(key, default)  # get the value; set default if not existent
        self.print_if_verbose(key)
        self.unused.discard(key)  # (does nothing if key not in set)
        return val

    def print_if_verbose(self, key, action=None):
        """Print out `key` if verbosity and other conditions are met.
        
        Parameters
        ----------
        key : str
            Key name for the parameter being read out.
        action : str, optional
            Use to adapt printout message to specific actions (e.g. "Deleting")
        """
        val = self.params[key]
        name = self.name
        verbose = self.verbose
        use_default = key not in self.params
        new_key = key in self.unused
        if verbose >= 100 or (new_key and verbose >= (2. if use_default else 1.)):
            actionstring = "Parameter" if action is None else action + " "
            defaultstring = "(default) " if use_default else ""
            print("{actionstring} {key!r}={val!r} {defaultstring}for {name!s}".format(
                actionstring=actionstring, name=name, key=key, val=val, defaultstring=defaultstring))

    def help(self, keys=None):
        """Reproduce documentation for `keys`.
        
        Parameters
        ----------
        keys : None | str | list, optional
            Which key(s) to describe
        """
        if keys is None:  # Assume you want all documentation.
            for key in self.params:
                self.print_documentation(key)
        elif isinstance(keys, list):
            for key in keys:
                self.print_documentation(key)
        else:
            self.print_documentation(keys)

    def print_documentation(self, key):
        if not key in self.documentation:
            print("No documentation for parameter {}.".format(key))
        else:
            doc = self.documentation[key]
            output = "{key!r} : {type_info!r} \n\t{help}"
            print(output.format(key=key, type_info=doc['type_info'], help=doc['help']))

    def document(self, key, type_info, help_text):
        """Add documentation for a parameter
        
        Parameters
        ----------
        key : str
            Name of the parameter
        type_info : str
            Type description of the parameter
        help_text : str
            Description of the parameter.
        """
        self.documentation[key] = {'type_info': type_info, 'help': help_text}

    def save_yaml(self, filename):
        """Save a representation of `self` to `filename` as a YAML file
        
        Parameters
        ----------
        filename : str
            Name of the resulting YAML file.
        """
        with open(filename, 'w') as stream:
            try:
                yaml.dump(self, stream)
            except yaml.YAMLError as err:
                print("Reading from YAML file encountered an error:")
                print(err)

    @classmethod
    def from_yaml(cls, filename):
        """Load a `Parameters` instance from a YAML file at `filename`.


        .. warning ::
            It is not safe to call :method:`~tenpy.tools.params.Parameters.from_yaml()` 
            with any data received from an untrusted source! This method may 
            call any Python function and should thus be treated with extreme 
            caution.
        
        Parameters
        ----------
        filename : str
            Name of the YAML file
        
        Returns
        -------
        obj : Parameters
            A `Parameters` object, loaded from file.
        """
        with open(filename, 'r') as stream:
            try:
                return yaml.load(stream, Loader=yaml.Loader)
            except yaml.YAMLError as err:
                print("Reading from YAML file encountered an error:")
                print(err)


def get_parameter(params, key, default, descr, asarray=False):
    """Read out a parameter from the dictionary and/or provide default values.

    This function provides a similar functionality as ``params.get(key, default)``.
    *Unlike* `dict.get` this function writes the default value into the dictionary
    (i.e. in other words it's more similar to ``params.setdefault(key, default)``).

    This allows the user to save the modified dictionary as meta-data, which gives a
    concrete record of the actually used parameters and simplifies reproducing the results
    and restarting simulations.

    Moreover, a special entry with the key ``'verbose'`` *in* the `params`
    can trigger this function to also print the used value.
    A higer `verbose` level implies more output.
    If `verbose` >= 100, it is printed every time it's used.
    If `verbose` >= 2., its printed for the first time time its used.
    and for `verbose` >= 1, non-default values are printed the first time they are used.
    otherwise only for the first use.

    Internally, whether a parameter was used is saved in the set ``params['_used_param']``.
    This is used in :func:`unused_parameters` to print a warning if the key wasn't used
    at the end of the algorithm, to detect mis-spelled parameters.

    Parameters
    ----------
    params : dict
        A dicionary of the parameters as provided by the user.
        If `key` is not a valid key, ``params[key]`` is set to `default`.
    key : string
        The key for the parameter which should be read out from the dictionary.
    default :
        The default value for the parameter.
    descr : str
        A short description for verbose output, like 'TEBD', 'XXZ_model', 'truncation'.
    asarray : bool
        If True, convert the result to a numpy array with ``np.asarray(...)`` before returning.

    Returns
    -------
    value :
        ``params[key]`` if the key is in params, otherwise `default`.
        Converted to a numpy array, if `asarray`.

    Examples
    --------
    In the algorith
    :class:`~tenpy.algorithms.tebd.Engine` gets a dictionary of parameters.
    Beside doing other stuff, it calls :meth:`tenpy.models.model.NearestNeighborModel.calc_U_bond`
    with the dictionary as argument, which looks similar like:

    >>> def model_calc_U(U_param):
    >>>    dt = get_parameter(U_param, 'dt', 0.01, 'TEBD')
    >>>    # ... calculate exp(-i * dt* H) ....

    Then, when you call `time_evolution` without any parameters, it just uses the default value:

    >>> tenpy.algorithms.tebd.time_evolution(..., dict())  # uses dt=0.01

    If you provide the special keyword ``'verbose'`` you can triger this function to print the
    used parameter values:

    >>> tenpy.algorithms.tebd.time_evolution(..., dict(verbose=1))
    parameter 'dt'=0.01 (default) for TEBD

    Of course you can also provide the parameter to use a non-default value:

    >>> tenpy.algorithms.tebd.time_evolution(..., dict(dt=0.1, verbose=1))
    parameter 'dt'=0.1 for TEBD
    """
    msg = ("Old-style parameter dictionaries are deprecated in favor of "
           "`Parameter` class objects. Use `Parameter` methods to read out"
           "parameters.")
    warnings.warn(msg, category=FutureWarning, stacklevel=2)
    if isinstance(params, Parameter):
        return params.get(key, default)
    use_default = key not in params
    val = params.setdefault(key, default)  # get the value; set default if not existent
    used = params.setdefault('_used_param', set())
    verbose = params.get('verbose', 0)
    new_key = key not in used
    if verbose >= 100 or (new_key and verbose >= (2. if use_default else 1.)):
        defaultstring = "(default) " if use_default else ""
        print("parameter {key!r}={val!r} {defaultstring}for {descr!s}".format(
            descr=descr, key=key, val=val, defaultstring=defaultstring))
    used.add(key)  # (does nothing if already present)
    if asarray:
        val = np.asarray(val)
    return val


def unused_parameters(params, warn=None):
    """Returns a set of the parameters which have not been read out with `get_parameters`.

    This function might be useful to check for typos in the parameter keys.

    Parameters
    ----------
    params : dict
        A dictionary of parameters which was given to (functions using) :meth:`get_parameter`
    warn : None | str
        If given, print a warning "unused parameter for {warn!s}: {unused_keys!s}".

    Returns
    -------
    unused_keys : set
        The set of keys of the params which was not used
    """
    msg = ("Old-style parameter dictionaries are deprecated in favor of "
           "`Parameter` class objects. Use `Parameter.unused` attribute to"
           "get unused parameters.")
    warnings.warn(msg, category=FutureWarning, stacklevel=2)
    if isinstance(params, Parameter):
        return params.unused
    used = params.get('_used_param', set())
    unused = set(params.keys()) - used
    unused.discard('_used_param')
    unused.discard('verbose')
    if warn is not None:
        if len(unused) > 0:
            if len(unused) > 1:
                msg = "unused parameters for {descr!s}:\n{keys!s}"
            else:
                msg = "unused parameter {keys!s} for {descr!s}\n"
            warnings.warn(msg.format(keys=sorted(unused), descr=warn))
    return unused
