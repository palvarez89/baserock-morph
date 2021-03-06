# Copyright (C) 2013-2015  Codethink Limited
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.


import json
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import uuid

import cliapp
import morphlib


class DeployPlugin(cliapp.Plugin):

    def enable(self):
        group_deploy = 'Deploy Options'
        self.app.settings.boolean(['upgrade'],
                                  'specify that you want to upgrade an '
                                  'existing cluster. Deprecated: use the '
                                  '`morph upgrade` command instead',
                                  group=group_deploy)
        self.app.add_subcommand(
            'deploy', self.deploy,
            arg_synopsis='CLUSTER [DEPLOYMENT...] [SYSTEM.KEY=VALUE]')
        self.app.add_subcommand(
            'upgrade', self.upgrade,
            arg_synopsis='CLUSTER [DEPLOYMENT...] [SYSTEM.KEY=VALUE]')

    def disable(self):
        pass

    def deploy(self, args):
        '''Deploy a built system image or a set of images.

        Command line arguments:

        * `CLUSTER` is the name of the cluster to deploy.

        * `DEPLOYMENT...` is the name of zero or more deployments in the
          morphology to deploy. If none are specified then all deployments
          in the morphology are deployed.

        * `SYSTEM.KEY=VALUE` can be used to assign `VALUE` to a parameter
          named `KEY` for the system identified by `SYSTEM` in the cluster
          morphology (see below). This will override parameters defined
          in the morphology.

        Morph deploys a set of systems listed in a cluster morphology.
        "Deployment" here is quite a general concept: it covers anything
        where a system image is taken, configured, and then put somewhere
        where it can be run. The deployment mechanism is quite flexible,
        and can be extended by the user.

        A cluster morphology defines a list of systems to deploy, and
        for each system a list of ways to deploy them. It contains the
        following fields:

        * **name**: MUST be the same as the basename of the morphology
         filename, sans .morph suffix.

        * **kind**: MUST be `cluster`.

        * **systems**: a list of systems to deploy;
         the value is a list of mappings, where each mapping has the
         following keys:

           * **morph**: the system morphology to use in the specified
             commit.

           * **deploy**: a mapping where each key identifies a
             system and each system has at least the following keys:

               * **type**: identifies the type of development e.g. (kvm,
                 nfsboot) (see below).
               * **location**: where the deployed system should end up
                 at. The syntax depends on the deployment type (see below).
                 Any additional item on the dictionary will be added to the
                 environment as `KEY=VALUE`.

            * **deploy-defaults**: allows multiple deployments of the same
             system to share some settings, when they can. Default settings
             will be overridden by those defined inside the deploy mapping.

         # Example

            name: cluster-foo
            kind: cluster
            systems:
                - morph: devel-system-x86_64-generic.morph
                  deploy:
                      cluster-foo-x86_64-1:
                          type: kvm
                          location: kvm+ssh://user@host/x86_64-1/x86_64-1.img
                          HOSTNAME: cluster-foo-x86_64-1
                          DISK_SIZE: 4G
                          RAM_SIZE: 4G
                          VCPUS: 2
                - morph: devel-system-armv7-highbank
                  deploy-defaults:
                      type: nfsboot
                      location: cluster-foo-nfsboot-server
                  deploy:
                      cluster-foo-armv7-1:
                          HOSTNAME: cluster-foo-armv7-1
                      cluster-foo-armv7-2:
                          HOSTNAME: cluster-foo-armv7-2

        Each system defined in a cluster morphology can be deployed in
        multiple ways (`type` in a cluster morphology). Morph provides
        the following types of deployment:

        * `tar` where Morph builds a tar archive of the root file system.

        * `rawdisk` where Morph builds a raw disk image and sets up the
          image with a bootloader and configuration so that it can be
          booted. Disk size is set with `DISK_SIZE` (see below).

        * `virtualbox-ssh` where Morph creates a VirtualBox disk image,
          and creates a new virtual machine on a remote host, accessed
          over ssh.  Disk and RAM size are set with `DISK_SIZE` and
          `RAM_SIZE` (see below).

        * `kvm`, which is similar to `virtualbox-ssh`, but uses libvirt
          and KVM instead of VirtualBox.  Disk and RAM size are set with
          `DISK_SIZE` and `RAM_SIZE` (see below).

        * `nfsboot` where Morph creates a system to be booted over
          a network.

        * `ssh-rsync` where Morph copies a binary delta over to the target
          system and arranges for it to be bootable. This requires
          `system-version-manager` from the tbdiff chunk

        * `initramfs`, where Morph turns the system into an initramfs image,
          suitable for being used as the early userland environment for a
          system to be able to locate more complicated storage for its root
          file-system, or on its own for diskless deployments.

        There are additional extensions that currently live in the Baserock
        definitions repo (baserock:baserock/definitions). These include:

        * `image-package` where Morph creates a tarball that includes scripts
          that can be used to make disk images outside of a Baserock
          environment. The example in definitions.git will create scripts for
          generating disk images and installing to existing disks.

        * `sdk` where Morph generates something resembing a BitBake SDK, which
          provides a toolchain for building software to target a system built
          by Baserock, from outside of a Baserock environment. This creates a
          self-extracting shell archive which you pass a directory to extract
          to, and inside that has a shell snippet called
          environment-setup-$TARGET which can be used to set environment
          variables to use the toolchain.

        * `pxeboot` where Morph temporarily network-boots the system you are
          deploying, so it can install a more permanent system onto local
          storage.

        In addition to the deployment type, the user must also give
        a value for `location`. Its syntax depends on the deployment
        types. The deployment types provided by Morph use the
        following syntaxes:

        * `tar`: pathname to the tar archive to be created; for
          example, `/home/alice/testsystem.tar`

        * `rawdisk`: pathname to the disk image to be created; for
          example, `/home/alice/testsystem.img`

        * `virtualbox-ssh` and `kvm`: a custom URL scheme that
          provides the target host machine (the one that runs
          VirtualBox or `kvm`), the name of the new virtual machine,
          and the location on the target host of the virtual disk
          file. The target host is accessed over ssh. For example,
          `vbox+ssh://alice@192.168.122.1/testsys/home/alice/testsys.vdi`
          or `kvm+ssh://alice@192.168.122.1/testsys/home/alice/testys.img`
          where

              * `alice@192.168.122.1` is the target as given to ssh,
                **from within the development host** (which may be
                different from the target host's normal address);

              * `testsys` is the new VM's name;

              * `/home/alice/testsys.vdi` and `/home/alice/testys.img` are
                the pathnames of the disk image files on the target host.

        * `nfsboot`: the address of the nfsboot server. (Note this is just
          the _address_ of the trove, _not_ `user@...`, since `root@` will
          automatically be prepended to the server address.)

        In addition to the `location`parameter, deployments can take additional
        `KEY=VALUE` parameters. These can be provided in the following ways:

        1. In the cluster definition file, e.g.

            ...
            systems:
            - morph: systems/foo-system.morph
              deploy:
                foo:
                  HOSTNAME: foo

        2.  In the environment before running e.g.

            `HOSTNAME=foo morph deploy ...`

        3.  On the command-line e.g.
         `morph deploy clusters/foo.morph foo.HOSTNAME=foo`

        For any boolean `KEY=VALUE` parameters, allowed values are:

        +ve `yes`, `1`, `true`;

        -ve `no`, `0`, `false`;

        The following `KEY=VALUE` parameters are supported for `rawdisk`,
        `virtualbox-ssh` and `kvm` and deployment types:

        * `DISK_SIZE=X` to set the size of the disk image. `X` should use a
          suffix of `K`, `M`, or `G` (in upper or lower case) to indicate
          kilo-, mega-, or gigabytes. For example, `DISK_SIZE=100G` would
          create a 100 gigabyte disk image. **This parameter is mandatory**.

        The `kvm` and `virtualbox-ssh` deployment types support an additional
        parameter:

        * `RAM_SIZE=X` to set the size of virtual RAM for the virtual
          machine. `X` is interpreted in the same was as `DISK_SIZE`,
          and defaults to `1G`.

        * `AUTOSTART=<VALUE>` - allowed values are `yes` and `no`
          (default)

        For the `nfsboot` write extension,

        * the following `KEY=VALUE` pairs are mandatory

              * `NFSBOOT_CONFIGURE=yes` (or any non-empty value). This
                enables the `nfsboot` configuration extension (see
                below) which MUST be used when using the `nfsboot`
                write extension.

              * `HOSTNAME=<STRING>` a unique identifier for that system's
                `nfs` root when it's deployed on the nfsboot server - the
                extension creates a directory with that name for the `nfs`
                root, and stores kernels by that name for the tftp server.

        * the following `KEY=VALUE` pairs are optional

              * `VERSION_LABEL=<STRING>` - set the name of the system
                version being deployed, when upgrading. Defaults to
                "factory".

        Each deployment type is implemented by a **write extension**. The
        ones provided by Morph are listed above, but users may also
        create their own by adding them in the same git repository
        and branch as the system morphology. A write extension is a
        script that does whatever is needed for the deployment. A write
        extension is passed two command line parameters: the name of an
        unpacked directory tree that contains the system files (after
        configuration, see below), and the `location` parameter.

        Regardless of the type of deployment, the image may be
        configured for a specific deployment by using **configuration
        extensions**. The extensions are listed in the system morphology
        file:

            ...
            configuration-extensions:
                - set-hostname

        The above specifies that the extension `set-hostname` is to
        be run.  Morph will run all the configuration extensions listed
        in the system morphology, and no others. (This way, configuration
        is more easily tracked in git.)

        Configuration extensions are scripts that get the unpacked
        directory tree of the system as their parameter, and do whatever
        is needed to configure the tree.

        Morph provides the following configuration extension built in:

        * `set-hostname` sets the hostname of the system to the value
          of the `HOSTNAME` variable.
        * `nfsboot` configures the system for nfsbooting. This MUST
          be used when deploying with the `nfsboot` write extension.

        Any `KEY=VALUE` parameters given in `deploy` or `deploy-defaults`
        sections of the cluster morphology, or given through the command line
        are set as environment variables when either the configuration or the
        write extension runs (except `type` and `location`).

        Deployment configuration is stored in the deployed system as
        /baserock/deployment.meta. THIS CONTAINS ALL ENVIRONMENT VARIABLES SET
        DURING DEPLOYMENT, so make sure you have no sensitive information in
        your environment that is being leaked. As a special case, any
        environment/deployment variable that contains 'PASSWORD' in its name is
        stripped out and not stored in the final system.

        '''

        # Nasty hack to allow deploying things of a different architecture
        def validate(self, root_artifact):
            pass
        morphlib.buildcommand.BuildCommand._validate_architecture = validate

        if not args:
            raise cliapp.AppException(
                'Too few arguments to deploy command (see help)')

        # Raise an exception if there is not enough space in tempdir
        # / for the path and 0 for the minimum size is a no-op
        # it exists because it is complicated to check the available
        # disk space given dirs may be on the same device
        morphlib.util.check_disk_available(
            self.app.settings['tempdir'],
            self.app.settings['tempdir-min-space'],
            '/', 0)

        cluster_filename = morphlib.util.sanitise_morphology_path(args[0])

        ws = morphlib.workspace.open('.')
        sb = morphlib.sysbranchdir.open_from_within('.')

        build_uuid = uuid.uuid4().hex

        build_command = morphlib.buildcommand.BuildCommand(self.app)
        build_command = self.app.hookmgr.call('new-build-command',
                                              build_command)
        loader = morphlib.morphloader.MorphologyLoader()
        name = morphlib.git.get_user_name(self.app.runcmd)
        email = morphlib.git.get_user_email(self.app.runcmd)
        build_ref_prefix = self.app.settings['build-ref-prefix']
        root_repo_dir = morphlib.gitdir.GitDirectory(
            sb.get_git_directory_name(sb.root_repository_url))
        cluster_text = root_repo_dir.read_file(cluster_filename)
        cluster_morphology = loader.load_from_string(cluster_text,
                                                     filename=cluster_filename)

        if cluster_morphology['kind'] != 'cluster':
            raise cliapp.AppException(
                "Error: morph deployment commands are only supported for "
                "cluster morphologies.")

        # parse the rest of the args
        all_subsystems = set()
        all_deployments = set()
        deployments = set()
        for system in cluster_morphology['systems']:
            all_deployments.update(system['deploy'].iterkeys())
            if 'subsystems' in system:
                all_subsystems.update(loader._get_subsystem_names(system))
        for item in args[1:]:
            if not item in all_deployments:
                break
            deployments.add(item)
        env_vars = args[len(deployments) + 1:]
        self.validate_deployment_options(
            env_vars, all_deployments, all_subsystems)

        if self.app.settings['local-changes'] == 'include':
            bb = morphlib.buildbranch.BuildBranch(sb, build_ref_prefix)
            pbb = morphlib.buildbranch.pushed_build_branch(
                    bb, loader=loader, changes_need_pushing=False,
                    name=name, email=email, build_uuid=build_uuid,
                    status=self.app.status)
            with pbb as (repo, commit, original_ref):
                self.deploy_cluster(build_command, cluster_morphology,
                                    root_repo_dir, repo, commit, env_vars,
                                    deployments)
        else:
            repo = sb.get_config('branch.root')
            ref = sb.get_config('branch.name')
            commit = root_repo_dir.resolve_ref_to_commit(ref)

            self.deploy_cluster(build_command, cluster_morphology,
                                root_repo_dir, repo, commit, env_vars,
                                deployments)

        self.app.status(msg='Finished deployment')

    def validate_deployment_options(
            self, env_vars, all_deployments, all_subsystems):
        for var in env_vars:
            for subsystem in all_subsystems:
                if subsystem == var:
                    raise cliapp.AppException(
                        'Cannot directly deploy subsystems. Create a top '
                        'level deployment for the subsystem %s instead.' %
                        subsystem)
                if (not any(deployment in var
                            for deployment in all_deployments)
                    and not subsystem in var):
                    raise cliapp.AppException(
                        'Variable referenced a non-existent deployment '
                        'name: %s' % var)

    def deploy_cluster(self, build_command, cluster_morphology, root_repo_dir,
                       repo, commit, env_vars, deployments):
        # Create a tempdir for this deployment to work in
        deploy_tempdir = tempfile.mkdtemp(
            dir=os.path.join(self.app.settings['tempdir'], 'deployments'))
        try:
            for system in cluster_morphology['systems']:
                self.deploy_system(build_command, deploy_tempdir,
                                   root_repo_dir, repo, commit, system,
                                   env_vars, deployments,
                                   parent_location='')
        finally:
            shutil.rmtree(deploy_tempdir)

    def deploy_system(self, build_command, deploy_tempdir,
                      root_repo_dir, build_repo, ref, system, env_vars,
                      deployment_filter, parent_location):
        sys_ids = set(system['deploy'].iterkeys())
        if deployment_filter and not \
                any(sys_id in deployment_filter for sys_id in sys_ids):
            return
        old_status_prefix = self.app.status_prefix
        system_status_prefix = '%s[%s]' % (old_status_prefix, system['morph'])
        self.app.status_prefix = system_status_prefix
        try:
            # Find the artifact to build
            morph = morphlib.util.sanitise_morphology_path(system['morph'])
            srcpool = build_command.create_source_pool(build_repo, ref, morph)

            artifact = build_command.resolve_artifacts(srcpool)

            deploy_defaults = system.get('deploy-defaults', {})
            for system_id, deploy_params in system['deploy'].iteritems():
                if not system_id in deployment_filter and deployment_filter:
                    continue
                deployment_status_prefix = '%s[%s]' % (
                    system_status_prefix, system_id)
                self.app.status_prefix = deployment_status_prefix
                try:
                    user_env = morphlib.util.parse_environment_pairs(
                            os.environ,
                            [pair[len(system_id)+1:]
                            for pair in env_vars
                            if pair.startswith(system_id)])

                    final_env = dict(deploy_defaults.items() +
                                     deploy_params.items() +
                                     user_env.items())

                    is_upgrade = ('yes' if self.app.settings['upgrade']
                                        else 'no')
                    final_env['UPGRADE'] = is_upgrade

                    deployment_type = final_env.pop('type', None)
                    if not deployment_type:
                        raise morphlib.Error('"type" is undefined '
                                             'for system "%s"' % system_id)

                    location = final_env.pop('location', None)
                    if not location:
                        raise morphlib.Error('"location" is undefined '
                                             'for system "%s"' % system_id)

                    morphlib.util.sanitize_environment(final_env)
                    self.check_deploy(root_repo_dir, ref, deployment_type,
                                      location, final_env)
                    system_tree = self.setup_deploy(build_command,
                                                    deploy_tempdir,
                                                    root_repo_dir,
                                                    ref, artifact,
                                                    deployment_type,
                                                    location, final_env)
                    for subsystem in system.get('subsystems', []):
                        self.deploy_system(build_command, deploy_tempdir,
                                           root_repo_dir, build_repo,
                                           ref, subsystem, env_vars, [],
                                           parent_location=system_tree)
                    if parent_location:
                        deploy_location = os.path.join(parent_location,
                                                       location.lstrip('/'))
                    else:
                        deploy_location = location
                    self.run_deploy_commands(deploy_tempdir, final_env,
                                             artifact, root_repo_dir,
                                             ref, deployment_type,
                                             system_tree, deploy_location)
                finally:
                    self.app.status_prefix = system_status_prefix
        finally:
            self.app.status_prefix = old_status_prefix

    def upgrade(self, args):
        '''Upgrade an existing set of instances using built images.

        See `morph help deploy` for documentation.

        '''

        if not args:
            raise cliapp.AppException(
                'Too few arguments to upgrade command (see `morph help '
                'deploy`)')

        if self.app.settings['upgrade']:
            raise cliapp.AppException(
                'Running `morph upgrade --upgrade` does not make sense.')

        self.app.settings['upgrade'] = True
        self.deploy(args)

    def check_deploy(self, root_repo_dir, ref, deployment_type, location, env):
        # Run optional write check extension. These are separate from the write
        # extension because it may be several minutes before the write
        # extension itself has the chance to raise an error.
        try:
            self._run_extension(
                root_repo_dir, deployment_type, '.check',
                [location], env)
        except morphlib.extensions.ExtensionNotFoundError:
            pass

    def setup_deploy(self, build_command, deploy_tempdir, root_repo_dir, ref,
                     artifact, deployment_type, location, env):
        # deployment_type, location and env are only used for saving metadata

        # Create a tempdir to extract the rootfs in
        system_tree = tempfile.mkdtemp(dir=deploy_tempdir)

        try:
            # Unpack the artifact (tarball) to a temporary directory.
            self.app.status(msg='Unpacking system for configuration')

            if build_command.lac.has(artifact):
                f = build_command.lac.get(artifact)
            elif build_command.rac.has(artifact):
                build_command.cache_artifacts_locally([artifact])
                f = build_command.lac.get(artifact)
            else:
                raise cliapp.AppException('Deployment failed as system is'
                                          ' not yet built.\nPlease ensure'
                                          ' the system is built before'
                                          ' deployment.')
            tf = tarfile.open(fileobj=f)
            tf.extractall(path=system_tree)

            self.app.status(
                msg='System unpacked at %(system_tree)s',
                system_tree=system_tree)

            self.app.status(
                msg='Writing deployment metadata file')
            metadata = self.create_metadata(
                    artifact, root_repo_dir, deployment_type, location, env)
            metadata_path = os.path.join(
                    system_tree, 'baserock', 'deployment.meta')
            with morphlib.savefile.SaveFile(metadata_path, 'w') as f:
                json.dump(metadata, f, indent=4,
                          sort_keys=True, encoding='unicode-escape')
            return system_tree
        except Exception:
            shutil.rmtree(system_tree)
            raise

    def run_deploy_commands(self, deploy_tempdir, env, artifact, root_repo_dir,
                            ref, deployment_type, system_tree, location):
        # Extensions get a private tempdir so we can more easily clean
        # up any files an extension left behind
        deploy_private_tempdir = tempfile.mkdtemp(dir=deploy_tempdir)
        env['TMPDIR'] = deploy_private_tempdir

        try:
            # Run configuration extensions.
            self.app.status(msg='Configure system')
            names = artifact.source.morphology['configuration-extensions']
            for name in names:
                self._run_extension(
                    root_repo_dir,
                    name,
                    '.configure',
                    [system_tree],
                    env)

            # Run write extension.
            self.app.status(msg='Writing to device')
            self._run_extension(
                root_repo_dir,
                deployment_type,
                '.write',
                [system_tree, location],
                env)

        finally:
            # Cleanup.
            self.app.status(msg='Cleaning up')
            shutil.rmtree(deploy_private_tempdir)

    def _report_extension_stdout(self, line):
        self.app.status(msg=line.replace('%s', '%%'))
    def _report_extension_stderr(self, error_list):
        def cb(line):
            error_list.append(line)
            sys.stderr.write('%s\n' % line)
        return cb
    def _report_extension_logger(self, name, kind):
        return lambda line: logging.debug('%s%s: %s', name, kind, line)
    def _run_extension(self, gd, name, kind, args, env):
        '''Run an extension.

        The ``kind`` should be either ``.configure`` or ``.write``,
        depending on the kind of extension that is sought.

        The extension is found either in the git repository of the
        system morphology (repo, ref), or with the Morph code.

        '''
        error_list = []
        with morphlib.extensions.get_extension_filename(name, kind) as fn:
            ext = morphlib.extensions.ExtensionSubprocess(
                report_stdout=self._report_extension_stdout,
                report_stderr=self._report_extension_stderr(error_list),
                report_logger=self._report_extension_logger(name, kind),
            )
            returncode = ext.run(fn, args, env=env, cwd=gd.dirname)
        if returncode == 0:
            logging.info('%s%s succeeded', name, kind)
        else:
            message = '%s%s failed with code %s: %s' % (
                name, kind, returncode, '\n'.join(error_list))
            raise cliapp.AppException(message)

    def create_metadata(self, system_artifact, root_repo_dir, deployment_type,
                        location, env):
        '''Deployment-specific metadata.

        The `build` and `deploy` operations must be from the same ref, so full
        info on the root repo that the system came from is in
        /baserock/${system_artifact}.meta and is not duplicated here. We do
        store a `git describe` of the definitions.git repo as a convenience for
        post-upgrade hooks that we may need to implement at a future date:
        the `git describe` output lists the last tag, which will hopefully help
        us to identify which release of a system was deployed without having to
        keep a list of SHA1s somewhere or query a Trove.

        '''

        def remove_passwords(env):
            is_password = morphlib.util.env_variable_is_password
            return { k:v for k, v in env.iteritems() if not is_password(k) }

        meta = {
            'system-artifact-name': system_artifact.name,
            'configuration': remove_passwords(env),
            'deployment-type': deployment_type,
            'location': location,
            'definitions-version': {
                'describe': root_repo_dir.describe(),
            },
            'morph-version': {
                'ref': morphlib.gitversion.ref,
                'tree': morphlib.gitversion.tree,
                'commit': morphlib.gitversion.commit,
                'version': morphlib.gitversion.version,
            },
        }

        return meta
