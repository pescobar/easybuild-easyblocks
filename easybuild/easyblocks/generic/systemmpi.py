##
# Copyright 2015-2017 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://www.vscentrum.be),
# Flemish Research Foundation (FWO) (http://www.fwo.be/en)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# http://github.com/hpcugent/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
##
"""
EasyBuild support for using (already installed/existing) system MPI instead of a full install via EasyBuild.

@author Alan O'Cais (Juelich Supercomputing Centre)
"""
import os
import re
from vsc.utils import fancylogger

from easybuild.easyblocks.generic.bundle import Bundle
from easybuild.easyblocks.generic.systemcompiler import extract_compiler_version
from easybuild.tools.modules import get_software_version
from easybuild.tools.filetools import read_file, which
from easybuild.tools.run import run_cmd
from easybuild.framework.easyconfig.easyconfig import ActiveMNS
from easybuild.tools.build_log import EasyBuildError

_log = fancylogger.getLogger('easyblocks.generic.systemmpi')

class SystemMPI(Bundle):
    """
    Support for generating a module file for the system mpi with specified name.

    The mpi compiler is expected to be available in $PATH, required libraries are assumed to be readily available.

    Specifying 'system' as a version leads to using the derived mpi version in the generated module;
    if an actual version is specified, it is checked against the derived version of the system mpi that was found.
    """

    def extract_ompi_setting(self, pattern, txt):
        """Extract a particular OpenMPI setting from provided string."""

        version_regex = re.compile(r'^\s+%s: (.*)$' % pattern, re.M)
        res = version_regex.search(txt)
        if res:
            setting = res.group(1)
            self.log.debug("Extracted OpenMPI setting %s: '%s' from search text", pattern, setting)
        else:
            raise EasyBuildError("Failed to extract OpenMPI setting '%s' using regex pattern '%s' from: %s",
                                 pattern, version_regex.pattern, txt)

        return setting

    def __init__(self, *args, **kwargs):
        """Extra initialization: determine system MPI version, prefix and any associated envvars."""
        super(SystemMPI, self).__init__(*args, **kwargs)

        mpi_name = self.cfg['name'].lower()

        # Determine MPI wrapper path (real path, with resolved symlinks) to ensure it exists
        if mpi_name == 'impi':
            mpi_c_wrapper = 'mpiicc'
        else:
            mpi_c_wrapper = 'mpicc'
        path_to_mpi_c_wrapper = which(mpi_c_wrapper)
        if path_to_mpi_c_wrapper:
            path_to_mpi_c_wrapper = os.path.realpath(path_to_mpi_c_wrapper)
            self.log.info("Found path to MPI implementation '%s' %s compiler (with symlinks resolved): %s",
                          mpi_name, mpi_c_wrapper, path_to_mpi_c_wrapper)
        else:
            raise EasyBuildError("%s not found in $PATH", mpi_c_wrapper)

        # Determine compiler version and installation prefix
        if mpi_name == 'openmpi':
            output_of_ompi_info, _ = run_cmd("ompi_info", simple=False)
            # Extract the version of OpenMPI
            self.mpi_version = self.extract_ompi_setting("Open MPI", output_of_ompi_info)

            # Extract the installation prefix
            self.mpi_prefix = self.extract_ompi_setting("Prefix", output_of_ompi_info)

            # Extract any OpenMPI environment variables in the current environment and ensure they are added to the
            # final module
            self.mpi_envvars = dict((key, value) for key, value in os.environ.iteritems() if key.startswith("OMPI_"))

            # Extract the C compiler used underneath OpenMPI, check for the definition of OMPI_MPICC
            self.mpi_c_compiler = self.extract_ompi_setting("C compiler", output_of_ompi_info)

        elif mpi_name == 'impi':
            # Extract the version of IntelMPI
            # The prefix in the the mpiicc script can be used to extract the explicit version
            contents_of_mpiicc = read_file(path_to_mpi_c_wrapper)
            prefix_regex = re.compile(r'(?<=compilers_and_libraries_)(.*)(?=/linux/mpi)', re.M)
            self.mpi_version = prefix_regex.search(contents_of_mpiicc).group(1)
            if self.mpi_version is not None:
                self.log.info("Found Intel MPI version %s for system MPI" % self.mpi_version)
            else:
                raise EasyBuildError("No version found for system Intel MPI")

            # Extract the installation prefix, if I_MPI_ROOT is defined, let's use that
            if os.environ.get('I_MPI_ROOT'):
                self.mpi_prefix = os.environ['I_MPI_ROOT']
            else:
                # Else just go up three directories from where mpiicc is found
                # (it's 3 because bin64 is a symlink to intel64/bin and we are assuming 64 bit)
                self.mpi_prefix = os.path.dirname(os.path.dirname(os.path.dirname(path_to_mpi_c_wrapper)))

            # Extract any IntelMPI environment variables in the current environment and ensure they are added to the
            # final module
            self.mpi_envvars = dict((key, value) for key, value in os.environ.iteritems() if key.startswith("I_MPI_"))
            self.mpi_envvars.update(dict((key, value) for key, value in os.environ.iteritems() if key.startswith("MPICH_")))
            self.mpi_envvars.update(
                dict((key, value) for key, value in os.environ.iteritems()
                     if key.startswith("MPI") and key.endswith("PROFILE"))
            )

            # Extract the C compiler used underneath Intel MPI
            compile_info, _ = run_cmd("%s -compile-info" % mpi_c_wrapper, simple=False)
            self.mpi_c_compiler = compile_info.split(' ', 1)[0]

        else:
            raise EasyBuildError("Unrecognised system MPI implementation %s", mpi_name)

        # Ensure install path of system MPI actually exists
        if not os.path.exists(self.mpi_prefix):
            raise EasyBuildError("Path derived for system MPI (%s) does not exist: %s!", mpi_name, self.mpi_prefix)

        self.log.debug("Derived version/install prefix for system MPI %s: %s, %s",
                       mpi_name, self.mpi_version, self.mpi_prefix)

        # For the version of the underlying C compiler need to explicitly extract (to be certain)
        self.c_compiler_version = extract_compiler_version(self.mpi_c_compiler)
        self.log.debug("Derived compiler/version for C compiler underneath system MPI %s: %s, %s",
                       mpi_name, self.mpi_c_compiler, self.c_compiler_version)

        # If EasyConfig specified "real" version (not 'system' which means 'derive automatically'), check it
        if self.cfg['version'] == 'system':
            self.log.info("Found specified version '%s', going with derived MPI version '%s'",
                          self.cfg['version'], self.mpi_version)
        elif self.cfg['version'] == self.mpi_version:
            self.log.info("Specified MPI version %s matches found version" % self.mpi_version)
        else:
            raise EasyBuildError("Specified version (%s) does not match version reported by MPI (%s)" %
                                 (self.cfg['version'], self.mpi_version))

        # fix installdir and module names (may differ because of changes to version)
        mns = ActiveMNS()
        self.cfg.full_mod_name = mns.det_full_module_name(self.cfg)
        self.cfg.short_mod_name = mns.det_short_module_name(self.cfg)
        self.cfg.mod_subdir = mns.det_module_subdir(self.cfg)

        # keep track of original values, for restoring later
        self.orig_version = self.cfg['version']
        self.orig_installdir = self.installdir

    def make_installdir(self, dontcreate=None):
        """Custom implementation of make installdir: do nothing, do not touch system MPI directories and files."""
        pass

    def make_module_req_guess(self):
        """
        A dictionary of possible directories to look for.  Return appropriate dict for system MPI.
        """
        if self.cfg['name'] ==  "impi":
            # Need some extra directories for Intel MPI, assuming 64bit here
            lib_dirs = ['lib/em64t', 'lib64']
            include_dirs = ['include64']
            return_dict = {
                'PATH': ['bin/intel64', 'bin64'],
                'LD_LIBRARY_PATH': lib_dirs,
                'LIBRARY_PATH': lib_dirs,
                'CPATH': include_dirs,
                'MIC_LD_LIBRARY_PATH': ['mic/lib'],
            }
        else:
            return_dict = {}

        return return_dict

    def make_module_step(self, fake=False):
        """
        Custom module step for SystemMPI: make 'EBROOT' and 'EBVERSION' reflect actual system MPI version
        and install path.
        """
        # First let's verify that the toolchain and the compilers under MPI match
        c_compiler_name = self.toolchain.COMPILER_CC
        if c_compiler_name == 'DUMMYCC':
            # If someone is using dummy as the MPI toolchain lets assume that gcc is the compiler underneath MPI
            c_compiler_name = 'gcc'
            # Also need to fake the compiler version
            compiler_version = self.c_compiler_version
            self.log.info("Found dummy toolchain so assuming GCC as compiler underneath MPI and faking the version")
        else:
            compiler_version = get_software_version(self.toolchain.COMPILER_MODULE_NAME[0])

        if self.mpi_c_compiler != c_compiler_name or self.c_compiler_version != compiler_version:
            raise EasyBuildError("C compiler for toolchain (%s/%s) and underneath MPI (%s/%s) do not match!",
                                 c_compiler_name, compiler_version, self.mpi_c_compiler, self.c_compiler_version)

        # For module file generation: temporarily set version and installdir to system compiler values
        self.cfg['version'] = self.mpi_version
        self.installdir = self.mpi_prefix

        # Generate module
        res = super(SystemMPI, self).make_module_step(fake=fake)

        # Reset version and installdir to EasyBuild values
        self.installdir = self.orig_installdir
        self.cfg['version'] = self.orig_version
        return res

    def make_module_extend_modpath(self):
        """
        Custom prepend-path statements for extending $MODULEPATH: use version specified in easyconfig file (e.g.,
        "system") rather than the actual version (e.g., "4.8.2").
        """
        # temporarly set switch back to version specified in easyconfig file (e.g., "system")
        self.cfg['version'] = self.orig_version

        # Retrieve module path extensions
        res = super(SystemMPI, self).make_module_extend_modpath()

        # Reset to actual MPI version (e.g., "4.8.2")
        self.cfg['version'] = self.mpi_version
        return res

    def make_module_extra(self):
        """Add all the extra environment variables we found."""
        txt = super(SystemMPI, self).make_module_extra()

        # include environment variables defined for MPI implementation
        for key, val in sorted(self.mpi_envvars.items()):
            txt += self.module_generator.set_environment(key, val)

        self.log.debug("make_module_extra added this: %s" % txt)
        return txt
