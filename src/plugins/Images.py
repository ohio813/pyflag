""" This module defines all the standard Image drivers within PyFlag """

import pyflag.IO as IO
import pyflag.DB as DB
from FlagFramework import query_type
import sk,re

class IOSubsysFD:
    def __init__(self, io, name):
        self.io = io
        self.readptr = 0
        self.name = name
        ## FIXME - dont lie here
        try:
            self.size = io.size
        except: self.size = 10000000000000
        
    def seek(self, offset, whence=0):
        """ fake seeking routine """
        if whence==0:
            readptr = offset
        elif whence==1:
            readptr+=offset
        elif whence==2:
            readptr = self.size

        if readptr<0:
            raise IOError("Seek before start of file")

        self.readptr = readptr

    def tell(self):
        """ return current read pointer """
        return self.readptr

    def read(self, length=0):
        """ read length bytes from subsystem starting at readptr """            
        buf = self.io.read_random(length,self.readptr)
        self.readptr += len(buf)
        return buf

    def close(self):
        """ close subsystem """
        pass

class Advanced(IO.Image):
    """ This is a IO source which provides access to raw DD images
    with offsets.
    """
    order = 20
    subsys = "advanced"
    io = None
    def calculate_partition_offset(self, query, result, offset = 'offset'):
        """ A GUI function to allow the user to derive the offset by calling mmls """
        def mmls_popup(query,result):
            result.decoration = "naked"

            try:
                del query[offset]
            except: pass
    
            ## Try creating the io source
            io = self.open(None, query['case'], query)
            try:
                parts = sk.mmls(io)
            except IOError, e:
                result.heading("No Partitions found")
                result.text("Sleuthkit returned: %s" % e)
                return

            result.heading("Possible IO Sources")
            result.start_table(border=True)
            result.row("Chunk", "Start", "End", "Size", "Description")
            del query[offset]
            for i in range(len(parts)):
                new_query = query.clone()
                tmp = result.__class__(result)
                new_query[offset] = "%ds" % parts[i][0]
                tmp.link("%010d" % parts[i][0], new_query, pane='parent')
                result.row(i, tmp, "%010d" % (parts[i][0] + parts[i][1]), "%010d" % parts[i][1] , parts[i][2])
                result.end_table()
        
        tmp = result.__class__(result)
        tmp2 = result.__class__(result)
        tmp2.popup(mmls_popup,
                   "Survey the partition table",
                   icon="examine.png")

        tmp.row(tmp2,"Enter partition offset:")
        result.textfield(tmp,offset)

    def calculate_offset_suffix(self, offset):
        m=re.match("(\d+)([sSkKgGmM]?)", offset)
        if not m:
            raise IOError("I cant understand offset should be an int followed by s,k,m,g")

        suffix=m.group(2).lower()
        multiplier = 1

        if not suffix: multiplier=1
        elif suffix=='k':
            multiplier = 1024
        elif suffix=='m':
            multiplier=1024*1024
        elif suffix=='g':
            multiplier = 1024**3
        elif suffix=='s':
            multiplier = 512

        return int(m.group(1))* multiplier

    def form(self, query, result):
        result.fileselector("Select %s image:" % self.__class__.__name__.split(".")[-1], name="filename")
        self.calculate_partition_offset(query, result)

    def make_iosource_args(self, query):
        offset = self.calculate_offset_suffix(query.get('offset','0'))
        
        args = [['subsys', self.subsys],
                ['offset', offset]]
        
        for f in query.getarray('filename'):
            args.append(['filename', f])

        return args

    def create(self, name, case, query):
        """ Given an iosource name, returns a file like object which represents it.

        name can be None, in which case this is an anonymous source (not cached).
        """
        import iosubsys

        args = self.make_iosource_args(query)
        io = iosubsys.iosource(args)

        return io

    def open(self, name, case, query=None):
        """
        This function opens a new instance of a file like object using
        the underlying subsystem.

        When we first get instantiated, self.io is None. We check our
        parameters and then call create to obtain a new self.io. The
        IO subsystem then caches this object (refered to by case and
        name). Subsequent open calls will use the same object which
        will ideally use the same self.io to instantiate a new
        IOSubsysFD() for each open call.
        """
        if not self.io:
            dbh = DB.DBO(case)
            
            ## This basically checks that the query is sane.
            if query:
                ## Check that all our mandatory parameters have been provided:
                for p in self.mandatory_parameters:
                    if not query.has_key(p):
                        raise IOError("Mandatory parameter %s not provided" % p)

                ## Check that the name does not already exist:
                if name:
                    dbh.execute("select * from iosources where name = %r" , name)
                    if dbh.fetch():
                        raise IOError("An iosource of name %s already exists in this case" % name)

                    ## Try to make it
                    self.io = self.create(name, case, query)

                    ## If we get here we made it successfully so store in db:
                    dbh.insert('iosources',
                               name = query['iosource'],
                               type = self.__class__.__name__,
                               parameters = "%s" % query,
                               _fast = True)
                else:
                    self.io = self.create(name, case, query)

            ## No query provided, we need to fetch it from the db:
            else:
                dbh.check_index('iosources','name')
                dbh.execute("select parameters from iosources where name = %r" , name)
                row = dbh.fetch()
                self.io = self.create(name, case, query_type(string=row['parameters']))

        return IOSubsysFD(self.io, name)

class SGZip(Advanced):
    """ Sgzip is pyflags native image file format """
    subsys = 'sgzip'

class EWF(Advanced):
    """ EWF is used by other forensic packages like Encase or FTK """
    subsys = 'ewf'

import Store

class CachedIO(IOSubsysFD):
    """ This is a cached version of the IOSubsysFD for filesystems for
    which reading is expensive.

    This is used for example by the remote filesystem. Typically when
    reading a filesystem, the same blocks need to be read over and
    over - for example reading the superblock list etc. This helps to
    alleviate this problem by caching commonly read blocks.
    """
    cache = Store.Store()

    def read(self, length=0):
        ## try to get the data out of the cache:
        key = "%s%s" % (self.readptr,length)
        try:
            data = self.cache.get(key)
        except KeyError,e:
            data = self.io.read_random(length,self.readptr)
            self.cache.put(data, key=key)

        self.readptr += len(data)
        return data

class Remote(Advanced):
    """ This IO Source provides for remote access """
    mandatory_parameters = ['host','device']
    def form(self, query, result):
        ## Fill the query with some defaults:
        query.default('port','3533')
        
        result.textfield("Host",'host')
        result.textfield("Port",'port')
        result.textfield("Raw Device",'device')
        
        query['host']
        
        self.calculate_partition_offset(query, result)

    def create(self, name, case, query):
        import remote
        offset = self.calculate_offset_suffix(query.get('offset','0'))
        
        io = remote.remote(host = query['host'],
                           port = int(query.get('port', 3533)),
                           device = query['device'],
                           offset = offset)

        return io
    
import os, unittest,iosubsys,time
import pyflag.conf
config=pyflag.conf.ConfObject()
import pyflag.pyflagsh as pyflagsh

class RemoteIOSourceTests(unittest.TestCase):
    """ Test the Remote IO source implementation """
    def setUp(self):
        time.sleep(1)
        ## Start the remote server on the localhost
        slave_pid = os.spawnl(os.P_NOWAIT, config.FLAG_BIN + "/remote_server", "remote_server", "-s")

        print "slave run with pid %u" % slave_pid
        ## Try to avoid the race
        time.sleep(1)
        
    def test02RemoteIOSource(self):
        """ Test the remote iosource implementation """
        io1 = iosubsys.iosource([['subsys','advanced'],
                                 ['filename','%s/pyflag_stdimage_0.2' % config.UPLOADDIR]])
        
        ## get the remote fd:
        import remote

        r = remote.remote("127.0.0.1", config.UPLOADDIR + self.test_file)

        ## Test the remote source
        IO.test_read_random(io1,r, io1.size, 1000000, 100)

    test_case = "PyFlagTestCase"
    test_file = "/pyflag_stdimage_0.2"
    fstype = "Sleuthkit"
    
    def test01LoadingFD(self):
        """ Try to load a filesystem using the Remote source """
        pyflagsh.shell_execv(command="execute",
                             argv=["Case Management.Remove case",'remove_case=%s' % self.test_case])

        pyflagsh.shell_execv(command="execute",
                             argv=["Case Management.Create new case",'create_case=%s' % self.test_case])

        pyflagsh.shell_execv(command="execute",
                             argv=["Load Data.Load IO Data Source",'case=%s' % self.test_case,
                                   "iosource=test",
                                   "subsys=Remote",
                                   "filename=%s" % (self.test_file),
                                   ])
        pyflagsh.shell_execv(command="execute",
                             argv=["Load Data.Load Filesystem image",'case=%s' % self.test_case,
                                   "iosource=test",
                                   "fstype=%s" % self.fstype,
                                   "mount_point=/"])


## FIXME - to do
class Mounted(Advanced):
    """ Treat a mounted directory as an image """
    subsys = 'sgzip'
