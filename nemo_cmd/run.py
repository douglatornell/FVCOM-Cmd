# Copyright 2013-2015 The Salish Sea MEOPAR Contributors
# and The University of British Columbia

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#    http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SalishSeaCmd command plug-in for run sub-command.

Prepare for, execute, and gather the results of a run of the
Salish Sea NEMO model.
"""
import datetime
import logging
import math
import os
import socket
import subprocess

import cliff.command
import pathlib

from . import (
    api,
    lib,
)


__all__ = ['Run', 'run', 'td2hms']


log = logging.getLogger(__name__)


class Run(cliff.command.Command):
    """Prepare, execute, and gather results from a Salish Sea NEMO model run.
    """
    def get_parser(self, prog_name):
        parser = super(Run, self).get_parser(prog_name)
        parser.description = '''
            Prepare, execute, and gather the results from a Salish Sea NEMO-3.6
            run described in DESC_FILE and IO_DEFS.
            The results files from the run are gathered in RESULTS_DIR.

            If RESULTS_DIR does not exist it will be created.
        '''
        parser.add_argument(
            'desc_file', metavar='DESC_FILE',
            help='File path/name of run description YAML file')
        parser.add_argument(
            'iodefs', metavar='IO_DEFS',
            help='File path/name of NEMO IOM server defs file for run')
        parser.add_argument(
            'results_dir', metavar='RESULTS_DIR',
            help='directory to store results into')
        parser.add_argument(
            '--nemo3.4', dest='nemo34', action='store_true',
            help='''
            Do a NEMO-3.4 run;
            the default is to do a NEMO-3.6 run''')
        parser.add_argument(
            '-q', '--quiet', action='store_true',
            help="don't show the run directory path or job submission message")
        parser.add_argument(
            '--compress', action='store_true',
            help='compress results files')
        parser.add_argument(
            '--keep-proc-results', action='store_true',
            help="don't delete per-processor results files")
        parser.add_argument(
            '--compress-restart', action='store_true',
            help="compress restart file(s)")
        parser.add_argument(
            '--delete-restart', action='store_true',
            help="delete restart file(s)")
        return parser

    def take_action(self, parsed_args):
        """Execute the `salishsea run` sub-coomand.

        The message generated upon submission of the run to the queue
        manager is logged to the console.

        :arg parsed_args: Arguments and options parsed from the command-line.
        :type parsed_args: :class:`argparse.Namespace` instance
        """
        qsub_msg = run(
            parsed_args.desc_file, parsed_args.iodefs, parsed_args.results_dir,
            parsed_args.nemo34, parsed_args.quiet,
            parsed_args.keep_proc_results, parsed_args.compress,
            parsed_args.compress_restart, parsed_args.delete_restart,
        )
        if not parsed_args.quiet:
            log.info(qsub_msg)


def run(
    desc_file, iodefs, results_dir,
    nemo34=False,
    quiet=False,
    keep_proc_results=False,
    compress=False,
    compress_restart=False,
    delete_restart=False,
):
    """Create and populate a temporary run directory, and a run script,
    and submit the run to the queue manager.

    The temporary run directory is created and populated via the
    :func:`SalishSeaCmd.api.prepare` API function.
    The system-specific run script is stored in :file:`SalishSeaNEMO.sh`
    in the run directory.
    That script is submitted to the queue manager in a subprocess.

    :arg desc_file: File path/name of the YAML run description file.
    :type desc_file: str

    :arg iodefs: File path/name of the NEMO IOM server defs file for
                 the run.
    :type iodefs: str

    :arg results_dir: Path of the directory in which to store the run
                      results;
                      it will be created if it does not exist.
    :type results_dir: str

    :arg nemo34: Prepare a NEMO-3.4 run;
                 the default is to prepare a NEMO-3.6 run
    :type nemo34: boolean

    :arg quiet: Don't show the run directory path message;
                the default is to show the temporary run directory path.
    :type quiet: boolean

    :arg keep_proc_results: Don't delete per-processor results files;
                            defaults to :py:obj:`False`.
    :type keep_proc_results: boolean

    :arg compress: Compress results files;
                   defaults to :py:obj:`False`.
    :type compress: boolean

    :arg compress_restart: Compress restart file(s);
                           defaults to :py:obj:`False`.
    :type compress_restart: boolean

    :arg delete_restart: Delete restart file(s);
                         defaults to :py:obj:`False`.
    :type delete_restart: boolean

    :returns: Message generated by queue manager upon submission of the
              run script.
    :rtype: str
    """
    run_dir_name = api.prepare(desc_file, iodefs, nemo34)
    if not quiet:
        log.info('Created run directory {}'.format(run_dir_name))
    run_dir = pathlib.Path(run_dir_name).resolve()
    run_desc = lib.load_run_desc(desc_file)
    n_processors = lib.get_n_processors(run_desc)
    results_dir = pathlib.Path(results_dir)
    gather_opts = ''
    if compress:
        gather_opts = ' '.join((gather_opts, '--compress'))
    if keep_proc_results:
        gather_opts = ' '.join((gather_opts, '--keep-proc-results'))
    if compress_restart:
        gather_opts = ' '.join((gather_opts, '--compress-restart'))
    if delete_restart:
        gather_opts = ' '.join((gather_opts, '--delete-restart'))
    system = os.getenv('WGSYSTEM') or socket.gethostname().split('.')[0]
    batch_script = _build_batch_script(
        run_desc, desc_file, n_processors, results_dir, run_dir.as_posix(),
        gather_opts, system,
    )
    batch_file = run_dir/'SalishSeaNEMO.sh'
    with batch_file.open('wt') as f:
        f.write(batch_script)
    starting_dir = pathlib.Path.cwd()
    os.chdir(run_dir.as_posix())
    qsub_msg = subprocess.check_output(
        'qsub SalishSeaNEMO.sh'.split(), universal_newlines=True)
    os.chdir(starting_dir.as_posix())
    return qsub_msg


def _build_batch_script(
    run_desc, desc_file, n_processors, results_dir, run_dir, gather_opts,
    system,
):
    """Build the Bash script that will execute the run.

    :arg run_desc: Run description dictionary.
    :type run_desc: dict

    :arg desc_file: File path/name of the YAML run description file.
    :type desc_file: str

    :arg n_processors: Number of processors that the run will be executed on.
    :type n_processors: int

    :arg results_dir: Path of the directory in which to store the run
                      results;
                      it will be created if it does not exist.
    :type results_dir: str

    :arg run_dir: Path of the temporary run directory.
    :type run_dir: str

    :arg gather_opts: Option flags for the :command:`salishsea gather`
                      command in the batch script.
    :type gather_opts: str

    :arg system: Name of the system that the run will be executed on;
                 e.g. :kbd:`salish`, :kbd:`orcinus`
    :type system: str

    :returns: Bash script to execute the run.
    :rtype: str
    """
    script = u'#!/bin/bash\n'
    if system != u'nowcast0':
        try:
            email = run_desc['email']
        except KeyError:
            email = u'{user}@eos.ubc.ca'.format(user=os.getenv('USER'))
        script = u'\n'.join((
            script,
            u'{pbs_common}'
            u'{pbs_features}\n'
            .format(
                pbs_common=_pbs_common(
                    run_desc, n_processors, email, results_dir),
                pbs_features=_pbs_features(n_processors, system)
                )
        ))
    script = u'\n'.join((
        script,
        u'{defns}\n'
        u'{modules}\n'
        u'{execute}\n'
        u'{fix_permissions}\n'
        u'{cleanup}'
        .format(
            defns=_definitions(
                run_desc['run_id'], desc_file, run_dir, results_dir,
                gather_opts, system, n_processors),
            modules=_modules(system),
            execute=_execute(system),
            fix_permissions=_fix_permissions(),
            cleanup=_cleanup(),
        )
    ))
    return script


def _pbs_common(run_desc, procs, email, results_dir, pmem='2000mb'):
    try:
        td = datetime.timedelta(seconds=run_desc['walltime'])
    except TypeError:
        t = datetime.datetime.strptime(run_desc['walltime'], '%H:%M:%S').time()
        td = datetime.timedelta(
            hours=t.hour, minutes=t.minute, seconds=t.second)
    walltime = td2hms(td)
    pbs_directives = (
        u'#PBS -N {run_id}\n'
        u'#PBS -S /bin/bash\n'
        u'#PBS -l procs={procs}\n'
        u'# memory per processor\n'
        u'#PBS -l pmem={pmem}\n'
        u'#PBS -l walltime={walltime}\n'
        u'# email when the job [b]egins and [e]nds, or is [a]borted\n'
        u'#PBS -m bea\n'
        u'#PBS -M {email}\n'
        u'# stdout and stderr file paths/names\n'
        u'#PBS -o {results_dir}/stdout\n'
        u'#PBS -e {results_dir}/stderr\n'
    ).format(
        run_id=run_desc['run_id'],
        procs=procs,
        pmem=pmem,
        walltime=walltime,
        email=email,
        results_dir=results_dir,
    )
    return pbs_directives


def td2hms(timedelta):
    """Return a string that is the timedelta value formated as H:M:S
    with leading zeros on the minutes and seconds values.

    :arg timedelta: Time interval to format.
    :type timedelta: :py:obj:datetime.timedelta

    :returns: H:M:S string with leading zeros on the minutes and seconds
              values.
    :rtype: unicode
    """
    seconds = int(timedelta.total_seconds())
    periods = (
        ('hour', 60*60),
        ('minute', 60),
        ('second', 1),
    )
    hms = []
    for period_name, period_seconds in periods:
        period_value, seconds = divmod(seconds, period_seconds)
        hms.append(period_value)
    return u'{0[0]}:{0[1]:02d}:{0[2]:02d}'.format(hms)


def _pbs_features(n_processors, system):
    pbs_features = u''
    if system == 'jasper':
        ppn = 12
        nodes = math.ceil(n_processors / ppn)
        pbs_features = (
            u'#PBS -l feature=X5675\n'
            u'#PBS -l nodes={}:ppn={}\n'.format(nodes, ppn)
        )
    elif system == 'orcinus':
        pbs_features = (
            u'#PBS -l partition=QDR\n'
        )
    return pbs_features


def _definitions(
    run_id, run_desc_file, run_dir, results_dir, gather_opts, system, procs,
):
    if system in 'salish nowcast0'.split():
        home = u'${HOME}'
        mpirun = u'mpirun -n {procs}'.format(procs=procs)
        if system == 'nowcast0':
            mpirun = u' '.join((mpirun, '--hostfile', '${HOME}/mpi_hosts'))
    else:
        home = u'${PBS_O_HOME}'
        mpirun = u'mpirun'
    defns = (
        u'RUN_ID="{run_id}"\n'
        u'RUN_DESC="{run_desc_file}"\n'
        u'WORK_DIR="{run_dir}"\n'
        u'RESULTS_DIR="{results_dir}"\n'
        u'MPIRUN="{mpirun}"\n'
        u'GATHER="{salishsea_cmd} gather"\n'
        u'GATHER_OPTS="{gather_opts}"\n'
    ).format(
        run_id=run_id,
        run_desc_file=run_desc_file,
        run_dir=run_dir,
        results_dir=results_dir,
        mpirun=mpirun,
        salishsea_cmd=os.path.join(home, '.local/bin/salishsea'),
        gather_opts=gather_opts,
    )
    return defns


def _modules(system):
    modules = u''
    if system == 'jasper':
        modules = (
            u'module load application/python/2.7.3\n'
            u'module load library/netcdf/4.1.3\n'
            u'module load library/szip/2.1\n'
            u'module load application/nco/4.3.9\n'
        )
    elif system == 'orcinus':
        modules = (
            u'module load intel\n'
            u'module load intel/14.0/netcdf-4.3.3.1\n'
            u'module load intel/14.0/netcdf-fortran-4.4.0\n'
            u'module load intel/14.0/hdf5-1.8.15p1\n'
            u'module load python\n'
        )
    return modules


def _execute(system):
    mpirun_suffix = u' >>stdout 2>>stderr' if system == 'nowcast0' else u''
    script = (
        u'cd ${WORK_DIR}\n'
        u'echo "working dir: $(pwd)"\n'
        u'\n'
        u'echo "Starting run at $(date)"\n'
        u'mkdir -p ${RESULTS_DIR}\n')
    script += u'${{MPIRUN}} ./nemo.exe{}\n'.format(mpirun_suffix)
    script += (
        u'echo "Ended run at $(date)"\n'
        u'\n'
        u'echo "Results gathering started at $(date)"\n'
        u'${GATHER} ${GATHER_OPTS} ${RUN_DESC} ${RESULTS_DIR}\n'
        u'echo "Results gathering ended at $(date)"\n'
    )
    return script


def _fix_permissions():
    script = (
        u'chmod go+rx ${RESULTS_DIR}\n'
        u'chmod g+rw ${RESULTS_DIR}/*\n'
        u'chmod o+r ${RESULTS_DIR}/*\n'
    )
    return script


def _cleanup():
    script = (
        u'echo "Deleting run directory"\n'
        u'rmdir $(pwd)\n'
    )
    return script
