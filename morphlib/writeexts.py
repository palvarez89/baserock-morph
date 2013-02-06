# Copyright (C) 2012-2013  Codethink Limited
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


import cliapp
import os
import re
import time
import tempfile


class WriteExtension(cliapp.Application):

    '''A base class for deployment write extensions.
    
    A subclass should subclass this class, and add a
    ``process_args`` method.
    
    Note that it is not necessary to subclass this class for write
    extensions. This class is here just to collect common code for
    write extensions.
    
    '''
    
    def process_args(self, args):
        raise NotImplementedError()

    def status(self, **kwargs):
        '''Provide status output.
        
        The ``msg`` keyword argument is the actual message,
        the rest are values for fields in the message as interpolated
        by %.
        
        '''
        
        self.output.write('%s\n' % (kwargs['msg'] % kwargs))

    def get_disk_size(self):
        '''Parse disk size from environment.'''
        
        size = os.environ.get('DISK_SIZE', '1G')
        m = re.match('^(\d+)([kmgKMG]?)$', size)
        if not m:
            raise morphlib.Error('Cannot parse disk size %s' % size)

        factors = {
            '': 1,
            'k': 1024,
            'm': 1024**2,
            'g': 1024**3,
        }
        factor = factors[m.group(2).lower()]

        return int(m.group(1)) * factor

    def create_raw_disk_image(self, filename, size):
        '''Create a raw disk image.'''

        self.status(msg='Creating empty disk image')
        cliapp.runcmd(
            ['dd',
             'if=/dev/zero',
             'of=' + filename,
             'bs=1',
             'seek=%d' % size,
             'count=0'])

    def mkfs_btrfs(self, location):
        '''Create a btrfs filesystem on the disk.'''
        self.status(msg='Creating btrfs filesystem')
        cliapp.runcmd(['mkfs.btrfs', '-L', 'baserock', location])
        
    def mount(self, location):
        '''Mount the filesystem so it can be tweaked.
        
        Return path to the mount point.
        The mount point is a newly created temporary directory.
        The caller must call self.unmount to unmount on the return value.
        
        '''

        self.status(msg='Mounting filesystem')        
        tempdir = tempfile.mkdtemp()
        # FIXME: This hardcodes the loop device.
        cliapp.runcmd(['mount', '-o', 'loop=loop0', location, tempdir])
        return tempdir
        
    def unmount(self, mount_point):
        '''Unmount the filesystem mounted by self.mount.
        
        Also, remove the temporary directory.
        
        '''
        
        self.status(msg='Unmounting filesystem')
        cliapp.runcmd(['umount', mount_point])
        os.rmdir(mount_point)

    def create_factory(self, real_root, temp_root):
        '''Create the default "factory" system.'''

        factory = os.path.join(real_root, 'factory')

        self.status(msg='Creating factory subvolume')
        cliapp.runcmd(['btrfs', 'subvolume', 'create', factory])
        self.status(msg='Copying files to factory subvolume')
        cliapp.runcmd(['cp', '-a', temp_root + '/.', factory + '/.'])

        # The kernel needs to be on the root volume.
        self.status(msg='Copying boot directory to root subvolume')
        factory_boot = os.path.join(factory, 'boot')
        root_boot = os.path.join(real_root, 'boot')
        cliapp.runcmd(['cp', '-a', factory_boot, root_boot])

    def create_fstab(self, real_root):
        '''Create an fstab.'''

        self.status(msg='Creating fstab')        
        fstab = os.path.join(real_root, 'factory', 'etc', 'fstab')
        with open(fstab, 'w') as f:
            f.write('proc      /proc proc  defaults            0 0\n')
            f.write('sysfs     /sys  sysfs defaults            0 0\n')
            f.write('/dev/sda  /     btrfs defaults,rw,noatime 0 1\n')

    def install_extlinux(self, real_root):
        '''Install extlinux on the newly created disk image.'''

        self.status(msg='Creating extlinux.conf')
        config = os.path.join(real_root, 'extlinux.conf')
        with open(config, 'w') as f:
            f.write('default linux\n')
            f.write('timeout 1\n')
            f.write('label linux\n')
            f.write('kernel /boot/vmlinuz\n')
            f.write('append root=/dev/sda rootflags=subvol=factory '
                    'init=/sbin/init rw\n')

        self.status(msg='Installing extlinux')
        cliapp.runcmd(['extlinux', '--install', real_root])

        # FIXME this hack seems to be necessary to let extlinux finish
        cliapp.runcmd(['sync'])
        time.sleep(2)

