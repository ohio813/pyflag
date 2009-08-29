""" This module contains functions which are shared among many plugins """
# ******************************************************
# Copyright 2004: Commonwealth of Australia.
#
# Developed by the Computer Network Vulnerability Team,
# Information Security Group.
# Department of Defence.
#
# Michael Cohen <scudette@users.sourceforge.net>
#
# ******************************************************
#  Version: FLAG  $Version: 0.87-pre1 Date: Thu Jun 12 00:48:38 EST 2008$
# ******************************************************
#
# * This program is free software; you can redistribute it and/or
# * modify it under the terms of the GNU General Public License
# * as published by the Free Software Foundation; either version 2
# * of the License, or (at your option) any later version.
# *
# * This program is distributed in the hope that it will be useful,
# * but WITHOUT ANY WARRANTY; without even the implied warranty of
# * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# * GNU General Public License for more details.
# *
# * You should have received a copy of the GNU General Public License
# * along with this program; if not, write to the Free Software
# * Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
# ******************************************************
import pyflag.conf
config=pyflag.conf.ConfObject()
import pyflag.Registry as Registry
from pyflag.Scanner import *
import pyflag.Scanner as Scanner
import dissect
import struct,sys,cStringIO
import pyflag.DB as DB
from pyflag.FileSystem import File
import pyflag.IO as IO
import pyflag.FlagFramework as FlagFramework
import pyflag.aff4.aff4 as aff4

def IP2str(ip):
    """ Returns a string representation of the 32 bit network order ip """
    tmp = list(struct.unpack('=BBBB',struct.pack('=L',ip)))
    tmp.reverse()
    return ".".join(["%s" % i for i in tmp])
                
class NetworkScanner(BaseScanner):
    """ This is the base class for network scanners.

    Note that network scanners operate on discrete packets, where stream scanners operate on whole streams (and derive from StreamScannerFactory).
    """
    pass

class StreamScannerFactory(GenScanFactory):
    """ This is a scanner factory which allows scanners to only
    operate on streams.
    """

class StreamTypeScan:
    pass

## Below is the new implementation for PCAPScanner
import pyflag.Magic as Magic
import reassembler, pypcap
import pdb
from aff4.aff4_attributes import *

class PCAPScanner(GenScanFactory):
    """ A scanner for PCAP files. We reasemble streams and load them
    automatically. Note that this code creates map streams for
    forward, reverse and combined streams.
    """
    def scan(self, fd, factories, type, mime):
        if "PCAP" not in type: return
        
        def Callback(mode, packet, connection):
            if mode == 'est':
                if 'map' not in connection:
                    ip = packet.find_type("IP")

                    ## We can only get tcp or udp packets here
                    try:
                        tcp = packet.find_type("TCP")
                    except AttributeError:
                        tcp = packet.find_type("UDP")

                    base_urn = "/%s-%s/%s-%s/" % (
                        ip.source_addr, ip.dest_addr,
                        tcp.source, tcp.dest)

                    combined_stream = CacheManager.AFF4_MANAGER.create_cache_map(
                        fd.case, base_urn + "combined", timestamp = packet.ts_sec,
                        target = fd.urn)
                    
                    connection['reverse']['combined'] = combined_stream
                    connection['combined'] = combined_stream
                    
                    map_stream = CacheManager.AFF4_MANAGER.create_cache_map(
                        fd.case, base_urn + "forward", timestamp = packet.ts_sec,
                        target = fd.urn)
                    connection['map'] = map_stream

                    r_map_stream = CacheManager.AFF4_MANAGER.create_cache_map(
                        fd.case, base_urn + "reverse", timestamp = packet.ts_sec,
                        target = fd.urn)
                    connection['reverse']['map'] = r_map_stream

                    ## Add to connection table
                    map_stream.insert_to_table("connection_details",
                                               dict(reverse = r_map_stream.inode_id,
                                                    src_ip = ip.src,
                                                    src_port = tcp.source,
                                                    dest_ip = ip.dest,
                                                    dest_port = tcp.dest,
                                                    _ts_sec = "from_unixtime(%s)" % packet.ts_sec,
                                                    )
                                               )
                    ## Make sure we know they are related
                    aff4.oracle.set_inheritence(r_map_stream, map_stream)
                    aff4.oracle.set_inheritence(combined_stream, map_stream)
                    
            elif mode == 'data':
                try:
                    tcp = packet.find_type("TCP")
                except AttributeError:
                    tcp = packet.find_type("UDP")

                length = len(tcp.data)
                connection['map'].write_from("@", packet.offset + tcp.data_offset, length)
                connection['combined'].write_from("@", packet.offset + tcp.data_offset,
                                                  length)

            elif mode == 'destroy':
                if connection['map'].size > 0 or connection['reverse']['map'].size > 0:
                    map_stream = connection['map']
                    map_stream.close()

                    r_map_stream = connection['reverse']['map']
                    r_map_stream.close()

                    combined_stream = connection['combined']
                    combined_stream.close()
                    Magic.set_magic(self.case, combined_stream.inode_id,
                                    "Combined stream")

                    map_stream.set_attribute(PYFLAG_REVERSE_STREAM, r_map_stream.urn)
                    r_map_stream.set_attribute(PYFLAG_REVERSE_STREAM, map_stream.urn)

                    ## FIXME - this needs to be done out of process!!!
                    Scanner.scan_inode(self.case, map_stream.inode_id,
                                       factories)
                    Scanner.scan_inode(self.case, r_map_stream.inode_id,
                                       factories)
                    Scanner.scan_inode(self.case, combined_stream.inode_id,
                                       factories)
                    

        ## Create a tcp reassembler if we need it
        processor = reassembler.Reassembler(packet_callback = Callback)

        ## Now process the file
        try:
            pcap_file = pypcap.PyPCAP(fd)
        except IOError:
            pyflaglog.log(pyflaglog.WARNING,
                          DB.expand("%s does not appear to be a pcap file", fd.urn))
            return

        while 1:
            try:
                packet = pcap_file.dissect()
                processor.process(packet)
            except StopIteration: break

        del processor
