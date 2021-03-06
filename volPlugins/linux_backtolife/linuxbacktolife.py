"""
@author: Luca Doglione, Marco Senno
@license: 
@contact: 
"""
import pdb
import volatility.obj as obj
import volatility.debug as debug
import volatility.plugins.linux.common as linux_common
import volatility.plugins.linux.proc_maps as linux_proc_maps
import volatility.plugins.linux.find_file as linux_find_file
import volatility.plugins.linux.dump_map as linux_dump_map
import volatility.plugins.linux_elf_dump.elfdump as linux_elf_dump
import volatility.plugins.linux_dump_sock.linuxdumpsock as linux_dump_sock
import volatility.plugins.linux_dump_unix_sock.linuxdumpunixsock as linux_dump_unix_sock
import volatility.plugins.linux_dump_signals.linuxdumpsignals as linux_dump_signals
import volatility.plugins.linux_dump_auxv.linuxdumpauxv as linux_dump_auxv
import volatility.plugins.linux.info_regs as linux_info_regs
from volatility.renderers import TreeGrid
from volatility.renderers.basic import Address
import struct
import os
import json
from ctypes import *

#Plugin which generates different files in order to restore a process using CRIU
class linux_backtolife(linux_proc_maps.linux_proc_maps):
    """Generate images file for CRIU"""

    def __init__(self, config, *args, **kwargs):
        linux_proc_maps.linux_proc_maps.__init__(self, config, *args, **kwargs)
        self._config.add_option('DUMP-DIR', short_option = 'D', default = "./", help = 'Output directory', action = 'store', type = 'str')
    
    #Method for dumping a file to local disc extracting it from a memory dump
    def dumpFile(self, listF, task):
        toFind = len(listF)
        if toFind == 0:
            print "\t0 files have to be extracted"
            return
        else: 
            print "\t" + str(toFind) + " files have to be extracted"
                
    
        for name, inode_addr in listF.iteritems():
            inode = obj.Object("inode", offset = int(inode_addr, 0), vm = self.addr_space)
            try: 
                f = open(name, "wb")
            except IOError, e:
                debug.error("Unable to open output file (%s): %s" % (outfile, str(e)))

            for page in linux_find_file.linux_find_file(self._config).get_file_contents(inode):        
                f.write(page)

            f.close()
            print "\t{0} extracted".format(name)
    



    #Method for dumping elf file relative to the process
    def dumpElf(self, outfd):
        data = linux_elf_dump.linux_elf_dump(self._config).calculate()
        data = linux_elf_dump.linux_elf_dump(self._config).render_text(outfd, data)
    
    #Method for dumping sockets info relative to this process
    def dumpSock(self, task, sockets_type):
        data = linux_dump_sock.linux_dump_sock(self._config).get_sock_info(task, self.addr_space)
        inetFile = open("inetsk.json", "w")
        inetData = {"magic":"INETSK", 
                    "entries":[]}
        for key, value in data.iteritems():
            if "tcp_stream" in value.keys():
                stream = value["tcp_stream"]
                value.pop("tcp_stream", None)
                streamData = {"magic":"TCP_STREAM", "entries":[stream]}

                # When a process is running, the consumed CPU time needs to be recorded 
                # for the cfs scheduler. sum_exec_runtime is used for this purpose. 
                # We use this value in order to have an estimation of TCP timestamp
                streamData["entries"][0]["timestamp"] = long(task.se.sum_exec_runtime) 
                streamFile = open("tcp-stream-{0:x}.json".format(int(key)), "w")
                streamFile.write(json.dumps(streamData, indent=4, sort_keys=False))
                streamFile.close()

            inetData["entries"].append(value)
            sockets_type[value["id"]+1] = "INETSK"

        inetFile.write(json.dumps(inetData, indent=4, sort_keys=False))
        inetFile.close()

    #Method for dumping sockets info relative to this process
    def dumpUnixSock(self, task, sockets_type):
        data = linux_dump_unix_sock.linux_dump_unix_sock(self._config).get_sock_info(self.addr_space, task)
        unixFile = open("unixsk.json", "w")
        unixData = {"magic":"UNIXSK", 
                    "entries":[]}
        for value in data:
            unixData["entries"].append(value)

            if value["id"] != 0:
                sockets_type[value["id"]+1] = "UNIXSK"

        unixFile.write(json.dumps(unixData, indent=4, sort_keys=False))
        unixFile.close()

    #Method for dumping sigactions
    def dumpSignals(self, task):
        data = linux_dump_signals.linux_dump_signals(self._config).read_sigactions(task)
        sigactsData = {"magic":"SIGACT", "entries":data}

        print "\tWriting sigacts-{0}.json file".format(self._config.PID)
        sigactsFile = open("sigacts-{0}.json".format(self._config.PID), "w")
        sigactsFile.write(json.dumps(sigactsData, indent=4, sort_keys=False))
        sigactsFile.close()


    #Method for extracting registers values
    def readRegs(self, task):
        info_regs = linux_info_regs.linux_info_regs(self._config).calculate()
        
        extra_regs = {}
        float_regs = {}
        thread_core = {}
        pids = []
        for thread in task.threads():
            name = thread.comm
            pids.append(thread.pid)
            jRegs = {"fs_base": "{0:#x}".format(thread.thread.fs),
                    "gs_base": "{0:#x}".format(thread.thread.gs),
                    "fs": "{0:#x}".format(thread.thread.fsindex),
                    "gs": "{0:#x}".format(thread.thread.gsindex),
                    "es": "{0:#x}".format(thread.thread.es),
                    "ds": "{0:#x}".format(thread.thread.ds)}
            extra_regs[thread.pid] = jRegs
            
            #Reading st_space from memory Byte by Byte
            addr = int(thread.thread.fpu.state.fxsave.__str__())+32
            st_space_vect = []
            for i in range(0, 32):
                reverse = []
                dataByte = self.read_addr_range(task, addr, 4)
                for c in dataByte:
                    reverse.insert(0, "{0:02x}".format(ord(c)))
                
                reverse.insert(0, "0x")
                value = ''.join(reverse)
                st_space_vect.append(int(value, 16))
                addr += 4
            
            #Reading xmm_space from memory Byte by Byte
            addr = int(thread.thread.fpu.state.fxsave.__str__()) + 160
            xmm_space_vect = []
            for i in range(0, 64):
                reverse = []
                dataByte = self.read_addr_range(task, addr, 4)
                for c in dataByte:
                    reverse.insert(0, "{0:02x}".format(ord(c)))
                
                reverse.insert(0, "0x")
                value = ''.join(reverse)
                xmm_space_vect.append(int(value, 16))
                addr += 4
            
            
            #Reading ymmh_space from memory Byte by Byte
            addr = int(thread.thread.fpu.state.xsave.ymmh.__str__())
            ymmh_space_vect = []
            for i in range(0, 64):
                reverse = []
                dataByte = self.read_addr_range(task, addr, 4)
                for c in dataByte:
                    reverse.insert(0, "{0:02x}".format(ord(c)))
                
                reverse.insert(0, "0x")
                value = ''.join(reverse)
                ymmh_space_vect.append(int(value, 16))
                addr += 4
                
            #Reading Thread_core structures
            threadCoreData = {
                                "futex_rla": 0,
                                "futex_rla_len": 0,
                                "sched_nice":0,
                                "sched_policy":0,
                                "sas":{"ss_sp":int(thread.sas_ss_sp), "ss_size":int(thread.sas_ss_size), "ss_flags":2}, #flags not found 
                                "signals_p":{},
                                "creds":{
                                            "uid":int(thread.cred.uid.val),
                                            "gid":int(thread.cred.gid.val),
                                            "euid":int(thread.cred.euid.val),
                                            "egid":int(thread.cred.egid.val),
                                            "suid":int(thread.cred.suid.val),
                                            "sgid":int(thread.cred.sgid.val),
                                            "fsuid":int(thread.cred.fsuid.val),
                                            "fsgid":int(thread.cred.fsgid.val),
                                            "cap_inh":[],
                                            "cap_prm":[],
                                            "cap_eff":[],
                                            "cap_bnd":[],
                                            "secbits":int(thread.cred.securebits),
                                            "groups":[0]
                                        }
                                }

            #Reading Caps
            addr = int(thread.cred.cap_inheritable.__str__())
            for i in range(0,2):
                reverse = []
                dataByte = self.read_addr_range(task, addr, 4)
                for c in dataByte:
                    reverse.insert(0, "{0:02x}".format(ord(c)))
                    
                reverse.insert(0, "0x")
                value = ''.join(reverse)
                threadCoreData["creds"]["cap_inh"].append(int(value, 16))
                addr+=4
                
            addr = int(thread.cred.cap_permitted.__str__())
            for i in range(0,2):
                reverse = []
                dataByte = self.read_addr_range(task, addr, 4)
                for c in dataByte:
                    reverse.insert(0, "{0:02x}".format(ord(c)))
                    
                reverse.insert(0, "0x")
                value = ''.join(reverse)
                threadCoreData["creds"]["cap_prm"].append(int(value, 16))
                addr+=4
                
            addr = int(thread.cred.cap_effective.__str__())
            for i in range(0,2):
                reverse = []
                dataByte = self.read_addr_range(task, addr, 4)
                for c in dataByte:
                    reverse.insert(0, "{0:02x}".format(ord(c)))
                    
                reverse.insert(0, "0x")
                value = ''.join(reverse)
                threadCoreData["creds"]["cap_eff"].append(int(value, 16))
                addr+=4

            addr = int(thread.cred.cap_bset.__str__())
            for i in range(0,2):
                reverse = []
                dataByte = self.read_addr_range(task, addr, 4)
                for c in dataByte:
                    reverse.insert(0, "{0:02x}".format(ord(c)))
                    
                reverse.insert(0, "0x")
                value = ''.join(reverse)
                threadCoreData["creds"]["cap_bnd"].append(int(value, 16))
                addr+=4

            thread_core[thread.pid] = threadCoreData

            fpregsData = {"fpregs":{"cwd":int(thread.thread.fpu.state.fxsave.cwd),
                                    "swd":int(thread.thread.fpu.state.fxsave.swd),
                                    "twd":int(thread.thread.fpu.state.fxsave.twd),
                                    "fop":int(thread.thread.fpu.state.fxsave.fop),
                                    "rip":int(thread.thread.fpu.state.fxsave.rip),
                                    "rdp":0,#int(thread.thread.fpu.state.fxsave.rdp),
                                    "mxcsr":int(thread.thread.fpu.state.fxsave.mxcsr),
                                    "mxcsr_mask":int(thread.thread.fpu.state.fxsave.mxcsr_mask),
                                    "st_space":st_space_vect, #Bytes from memory
                                    "xmm_space":xmm_space_vect, #Bytes from memory
                                    "xsave":{
                                            "xstate_bv":int(thread.thread.fpu.state.xsave.xsave_hdr.xstate_bv),
                                            "ymmh_space":ymmh_space_vect #Bytes from memory
                                            }
                                    }
                        }
            float_regs[thread.pid] = fpregsData
        
        #Works only with 64bit registers
        i = 0
        for task, name, thread_regs in info_regs:
            for thread_name, regs in thread_regs:
                if regs != None:
                    pid = pids[i]
                    i+=1
                    print "\tWorking on thread: " + str(pid)
                    fCore = open("core-{0}.json".format(int(str(pid))), "w")
                    regsData = {"gpregs": {
                                    "r15": "{0:#x}".format(regs["r15"]),
                                    "r14": "{0:#x}".format(regs["r14"]),
                                    "r13": "{0:#x}".format(regs["r13"]),
                                    "r12": "{0:#x}".format(regs["r12"]),
                                    "bp": "{0:#x}".format(regs["rbp"]),
                                    "bx": "{0:#x}".format(regs["rbx"]),
                                    "r11": "{0:#x}".format(regs["r11"]),
                                    "r10": "{0:#x}".format(regs["r10"]),
                                    "r9": "{0:#x}".format(regs["r9"]),
                                    "r8": "{0:#x}".format(regs["r8"]),
                                    "ax": "{0:#x}".format(regs["unknown"]),
                                    "cx": "{0:#x}".format(regs["rcx"]),
                                    "dx": "{0:#x}".format(regs["rdx"]),
                                    "si": "{0:#x}".format(regs["rsi"]),
                                    "di": "{0:#x}".format(regs["rdi"]),
                                    "orig_ax": "{0:#x}".format(regs["unknown"]),
                                    "ip": "{0:#x}".format(int(regs["rip"])-2), ####CRIU
                                    "cs": "{0:#x}".format(regs["cs"]),
                                    "flags": "{0:#x}".format(regs["eflags"]),
                                    "sp": "{0:#x}".format(regs["rsp"]),
                                    "ss": "{0:#x}".format(regs["ss"]),
                                    "fs_base": extra_regs[pid]["fs_base"],
                                    "gs_base": extra_regs[pid]["gs_base"],
                                    "ds": extra_regs[pid]["ds"],
                                    "es": extra_regs[pid]["es"],
                                    "fs": extra_regs[pid]["fs"],
                                    "gs": extra_regs[pid]["gs"]
                                },
                                "fpregs": float_regs[pid]["fpregs"],
                                "clear_tid_addr": "0x0"
                    }
                    
                    tcData = {
                                "task_state": int(task.state),
                                "exit_code": int(task.exit_code),
                                "personality": int(task.personality),
                                "flags": int(task.flags), #It's different
                                "blk_sigset": "0x0", #Temporary
                                "comm": task.comm.__str__(),
                                "timers": {
                                            "real":{
                                                    "isec":0,
                                                    "iusec":0,
                                                    "vsec":0,
                                                    "vusec":0
                                                    },
                                            "virt":{
                                                    "isec":0,
                                                    "iusec":0,
                                                    "vsec":0,
                                                    "vusec":0
                                                    },
                                            "prof":{
                                                    "isec":0,
                                                    "iusec":0,
                                                    "vsec":0,
                                                    "vusec":0
                                                    }
                                            },
                                "rlimits": {}, #Local
                                "cg_set": 1, #Temporary
                                "signals_s":{}, #Empty for Nano
                                "loginuid": int(task.loginuid.val),
                                "oom_score_adj": int(task.signal.oom_score_adj)
                                } 
                    
                    if int(task.state) != 1 and int(task.state) != 2 and int(task.state) != 3:
                        tcData["task_state"] = 1


                    fCoreData = {
                                "magic": "CORE",
                                "entries":[
                                            {
                                                "mtype": "X86_64",
                                                "thread_info":regsData,
                                                "tc": tcData,
                                                "thread_core": thread_core[pid]
                                            }
                                        ]
                                }


                    if int(str(task.pid)) != int(str(pid)):
                        fCoreData["entries"][0].pop("tc", None)
                        fCoreData["entries"][0]["thread_core"]["blk_sigset"] = 0
                        


                    fCore.write(json.dumps(fCoreData, indent=4, sort_keys=False))
                    fCore.close()
               
               
               
    #Method for reading an address range in memory dump dividing in pages
    def read_addr_range_page(self, task, start, end):
        pagesize = 4096 
        proc_as = task.get_process_address_space()
        while start < end:
            page = proc_as.zread(start, pagesize)
            yield page
            start = start + pagesize

    #Method for reading an address range in memory dump
    def read_addr_range(self, task, start, size):
        proc_as = task.get_process_address_space()
        segment = proc_as.zread(start, size)
        return segment
    
    #Build pstree file for CRIU with info about process and his threads
    def buildPsTree(self, task):
        pstreeData = {"magic":"PSTREE", "entries":[{
                                                    "pid":int(str(task.pid)),
                                                    "ppid":0,
                                                    "pgid":int(str(task.pid)),
                                                    "sid":0
                                                    }]}
        threads = []
        for thread in task.threads():
            threads.append(int(str(thread.pid)))

        pstreeData["entries"][0]["threads"] = threads
        
        pstreeFile = open("pstree.json", "w")
        pstreeFile.write(json.dumps(pstreeData, indent=4))
        pstreeFile.close()

    #Generate string of PROT field starting from permission flags of a segment for MM file
    def protText(self, flag):
        prot = ""
        r = False
        if "r" in flag:
            prot += "PROT_READ"
            r = True

        if "w" in flag:
            if r:
                prot += " | "
            prot += "PROT_WRITE"
            r = True

        if "x" in flag:
            if r:
                prot += " | "
            prot += "PROT_EXEC"

        return prot

    #Method for generating flags string for MM file
    def flagsText(self, name):
        flags = ""
        
        #Cache is SHARED
        if ".cache" in name:
            flags += "MAP_SHARED"
            return flags
        
        #Other Segment are PRIVATE
        flags += "MAP_PRIVATE"
        
        #If Segment is not relative to any file it's ANON
        if name == "" or "[" in name:
            flags += " | MAP_ANON"
        
        #STACK is always GROWSDOWN
        if name == "[stack]":
            flags += " | MAP_GROWSDOWN"

        return flags
        
    #Method that can generate status String for MM file
    def statusText(self, name):
        flags = "VMA_AREA_REGULAR"
        
        if ".cache" in name:
            flags += " | VMA_FILE_SHARED"
            return flags
        
        if name != "" and not "[" in name:
            flags += " | VMA_FILE_PRIVATE"
                
        if name == "[heap]":
            flags += " | VMA_AREA_HEAP"
            
        if name == "[vdso]":
            flags += " | VMA_AREA_VDSO"
        
        if name == "" or "[" in name:
            flags += " | VMA_ANON_PRIVATE"

        return flags
    
    #Method for generating shmid, it takes max fd id assign it to program, and assign ids to other files
    def getShmid(self, progname, current_name, dic, task):
        if current_name == "" or "[" in current_name:
            return 0


        if progname not in dic:
            maxFd = 2
            for filp, fd in task.lsof(): 
                #self.table_row(outfd, Address(task.obj_offset), str(task.comm), task.pid, fd, linux_common.get_path(task, filp))
                if fd > maxFd and "/dev/pts/" not in linux_common.get_path(task, filp):
                    maxFd = fd
            

            dic[progname] = maxFd


        if current_name in dic:

            return dic[current_name]

        else:
            dic[current_name] = len(dic) + dic[progname]
            return dic[current_name]


    def dump_fd_info(self, task, sockets_type):

        fdinfoFile = open("fdinfo-2.json", "w")
        entries = []
        for file, fd in task.lsof():
            path = linux_common.get_path(task, file)
            element = {"id":0, "flags":0, "type":"", "fd":int(fd)}
            if "/dev/pts" in path:
                element["id"] = 1
                element["type"] = "TTY"
            elif "socket:[" in path:
                element["id"] = fd-1
                element["type"] = sockets_type[fd]
            else:
                element["id"] = fd-1
                element["type"] = "REG"

            entries.append(element)
        data = {"magic":"FDINFO", "entries":entries}
        fdinfoFile.write(json.dumps(data, indent=4, sort_keys=False))
        fdinfoFile.close()



    #Method that perform all the operations
    def render_text(self, outfd, data):
        if not self._config.PID:
            debug.error("You have to specify a process to dump. Use the option -p.\n")
        
        file_name = "pages-1.img"
        file_path = os.path.join(self._config.DUMP_DIR, file_name)
        
        progName = ""
        shmidDic = {}
        procFiles = {} #Files used in process
        procFilesExtr = {} #Files that have to be extracted
        
        print "Creating pages file for process with PID: " + self._config.PID
        buildJson = True
        
        pagemap = open("pagemap-{0}.json".format(self._config.PID), "w")
        pagemapData = {"magic":"PAGEMAP", "entries":[{"pages_id":1}]}
        
        mmFile = open("mm-{0}.json".format(self._config.PID), "w")
        mmData = {"magic":"MM", "entries":[{"mm_start_code": 0,
                                            "mm_end_code":0,
                                            "mm_start_data":0,
                                            "mm_end_data":0,
                                            "mm_start_stack":0,
                                            "mm_start_brk":0,
                                            "mm_brk":0,
                                            "mm_arg_start":0,
                                            "mm_arg_end":0,
                                            "mm_env_start":0,
                                            "mm_env_end":0,
                                            "exe_file_id":0,
                                            "vmas":[],
                                            "dumpable":1
                                            }]}
                                            
        regfilesFile = open("procfiles.json".format(self._config.PID), "w")
        regfilesData = {"entries":[], "pid":self._config.PID, "threads":[]}
        

        self.table_header(outfd, [("Start", "#018x"), ("End",   "#018x"), ("Number of Pages", "6"), ("File Path", "")])
        outfile = open(file_path, "wb")

        vmas = []

        for task, vma in data:
            savedTask = task
            (fname, major, minor, ino, pgoff) = vma.info(task)
            vmas.append(vma)
		#create heuristic for obtaining progname
            if "[" not in fname and ".so" not in fname and ino != 0 and ".cache" not in fname: 
                progName = fname


        for vma in vmas:
            (fname, major, minor, ino, pgoff) = vma.info(savedTask)
            vmasData = {"start":"{0:#x}".format(vma.vm_start),
                        "end":"{0:#x}".format(vma.vm_end),
                        "pgoff":pgoff,
                        "shmid":self.getShmid(progName, fname, shmidDic, savedTask),
                        "prot":"{0}".format(self.protText(str(vma.vm_flags))),
                        "flags":"{0}".format(self.flagsText(fname)),
                        "status":"{0}".format(self.statusText(fname)),
                        "fd":-1,
                        "fdflags":"0x0"
                        }
                        
            #If VDSO number of pages of predecessor node have to be incremented      
            if fname == "[vdso]":
                mmData["entries"][0]["vmas"][len(mmData["entries"][0]["vmas"])-1]["status"] += " | VMA_AREA_VVAR"
                pagemapData["entries"][len(pagemapData["entries"])-1]["nr_pages"] += 2
                
            mmData["entries"][0]["vmas"].append(vmasData)

            #if Inode != 0, it's a file which have to be linked
            if ino != 0 and fname not in procFiles:
                procFiles[fname] = True
                idF = vmasData["shmid"]
                typeF = "local"
                nameF = fname
                if fname == progName:
                    #ELF is extracted
                    typeF = "elf"
                    nameF = savedTask.comm + ".dump"
                    
                    
                
                fileE = {"name":nameF, "id": idF, "type":typeF}
                regfilesData["entries"].append(fileE)

            #Shared Lib in exec mode not have to be dumped
            exLib = ".so" in fname and "x" in str(vma.vm_flags)

            #DUMP only what CRIU needs
            if str(vma.vm_flags) != "---" and fname != "[vdso]" and ".cache" not in fname and not exLib and "/lib/locale/" not in fname:
                npage = 0
                for page in self.read_addr_range_page(savedTask, vma.vm_start, vma.vm_end):
                    outfile.write(page)
                    npage +=1
                pagemapData["entries"].append({"vaddr":"{0:#x}".format(vma.vm_start), "nr_pages":npage})
                self.table_row(outfd,vma.vm_start, vma.vm_end, npage, fname)
                
        outfile.close()

        #set Limit addresses for MM file
        print "Reading address ranges and setting limits"
        mm = savedTask.mm
        
        mmData["entries"][0]["mm_start_code"] = "{0:#x}".format(mm.start_code)
        mmData["entries"][0]["mm_end_code"] = "{0:#x}".format(mm.end_code)
        mmData["entries"][0]["mm_start_data"] = "{0:#x}".format(mm.start_data)
        mmData["entries"][0]["mm_end_data"] = "{0:#x}".format(mm.end_data)
        mmData["entries"][0]["mm_start_stack"] = "{0:#x}".format(mm.start_stack)
        mmData["entries"][0]["mm_start_brk"] = "{0:#x}".format(mm.start_brk)
        mmData["entries"][0]["mm_brk"] = "{0:#x}".format(mm.brk)
        mmData["entries"][0]["mm_arg_start"] = "{0:#x}".format(mm.arg_start)
        mmData["entries"][0]["mm_arg_end"] = "{0:#x}".format(mm.arg_end)
        mmData["entries"][0]["mm_env_start"] = "{0:#x}".format(mm.env_start)
        mmData["entries"][0]["mm_env_end"] = "{0:#x}".format(mm.env_end)
        mmData["entries"][0]["exe_file_id"] = shmidDic[progName]
        
        #Reading Auxilary Vector
        print "Reading Auxiliary Vector"
        saved_auxv = linux_dump_auxv.linux_dump_auxv(self._config).read_auxv(task)[:38]
        mmData["entries"][0]["mm_saved_auxv"] = saved_auxv

        #Files used by process: TYPE = EXTRACTED
        for filp, fd in task.lsof():
            fpath = linux_common.get_path(task, filp)
            if "/dev/pts" not in fpath:
                if "/" not in fpath:
                    if "socket:[" in fpath:
                        regfilesData["sockets"] = []
                        regfilesData["sockets"].append(fd-1)
                    continue
                else:
                    fname = fpath.replace("/", "_")
                    fname += str(fd)
                    typeF = "extracted"
                    idF = fd -1
                    fileE = {"name":fname, "id": idF, "type":typeF, "pos":long(filp.f_pos)}
                    regfilesData["entries"].append(fileE)
                    procFilesExtr[fname] = "{0:#x}".format(filp.f_inode)
                 
 
        print "Extracting Files: " 
        self.dumpFile(procFilesExtr, savedTask)

        print "Building PsTree"
        self.buildPsTree(savedTask)

        for thread in savedTask.threads():
            regfilesData["threads"].append(int(str(thread.pid)))

        print "Searching registers values and threads states"
        self.readRegs(savedTask)

        pagemap.write(json.dumps(pagemapData, indent=4, sort_keys=False))
        pagemap.close()

        mmFile.write(json.dumps(mmData, indent=4, sort_keys=False))
        mmFile.close()
        
        regfilesFile.write(json.dumps(regfilesData, indent=4, sort_keys=False))
        regfilesFile.close()

        print "Searching Signal Handler and sigactions"
        self.dumpSignals(savedTask)

        sockets_type = {}

        print "Dumping Sockets"
        self.dumpSock(savedTask, sockets_type)

        print "Dumping Unix Sockets"
        self.dumpUnixSock(savedTask, sockets_type)

        print "Extracting File Descriptors info"
        self.dump_fd_info(savedTask, sockets_type)

        print "Dumping ELF file"
        self.dumpElf(outfd)

