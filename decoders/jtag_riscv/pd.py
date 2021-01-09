##
## Work in progress RISC-V stack for JTAG decoder.
## Still in initial stages.
## Lots of dead code from arm decoder. Lots of bugs, not much working

import sigrokdecode as srd

# From RISC-V External Debug Support version 0.13.2 part 6.1.2
ir = {
    '00000': ['BYPASS', 1], # Bypass register
    '00001': ['IDCODE', 32], # Bypass register
    '10000': ['dtmcs', 35], # DTM Control and Status (dtmcs)
    '10001': ['dmi', 35],  #  Debug Module Interface Access (dmi)
    '10010': ['RESERVED', 35],  # Bypass register
    '10011': ['RESERVED', 35],  # Bypass register
    '10100': ['RESERVED', 35],  # Bypass register
    '10101': ['RESERVED', 35],  # Bypass register
    '10110': ['RESERVED', 35],  # Bypass register
    '11111': ['BYPASS', 35],  # Bypass register
}

# ARM Cortex-M3 r1p1-01rel0 ID code
cm3_idcode = 0x3ba00477

# http://infocenter.arm.com/help/topic/com.arm.doc.ddi0413c/Chdjibcg.html
cm3_idcode_ver = {
    0x3: 'JTAG-DP',
    0x2: 'SW-DP',
}
cm3_idcode_part = {
    0xba00: 'JTAG-DP',
    0xba10: 'SW-DP',
}

# http://infocenter.arm.com/help/topic/com.arm.doc.faqs/ka14408.html
jedec_id = {
    5: {
        0x3b: 'ARM Ltd.',
    },
}

# ACK[2:0] in the DPACC/APACC registers (unlisted values are reserved)
ack_val = {
    '001': 'WAIT',
    '010': 'OK/FAULT',
}

# 32bit debug port registers (addressed via A[3:2])
dp_reg = {
    '00': 'Reserved', # Must be kept at reset value
    '01': 'DP CTRL/STAT',
    '10': 'DP SELECT',
    '11': 'DP RDBUFF',
}

# APB-AP registers (each of them 32 bits wide)
apb_ap_reg = {
    0x00: ['CSW', 'Control/status word'],
    0x04: ['TAR', 'Transfer address'],
    # 0x08: Reserved SBZ
    0x0c: ['DRW', 'Data read/write'],
    0x10: ['BD0', 'Banked data 0'],
    0x14: ['BD1', 'Banked data 1'],
    0x18: ['BD2', 'Banked data 2'],
    0x1c: ['BD3', 'Banked data 3'],
    # 0x20-0xf4: Reserved SBZ
    0x800000000: ['ROM', 'Debug ROM address'],
    0xfc: ['IDR', 'Identification register'],
}

# Bits[31:28]: Version 
# Bits[27:12]: Part number
# Bits[11:1]:  Manufacturer ID. Bits 6:0 must be bits 6:0 of the designer/manufacturer’s 
#   Identification Code as assigned by JEDEC Standard JEP106. Bits 10:7 contain the
#   modulo-16 count of the number of continuation characters (0x7f) in that same Identification Code.
# Bits[0:0]:   Reserved (0x1)
def decode_device_id_code(bits):
    version = bits[-32:-28]
    part_number = bits[-28:-12]
    manuf_id = bits[-11:-1]
    return (manuf_id, version, part_number)

# DPACC is used to access debug port registers (CTRL/STAT, SELECT, RDBUFF).
# APACC is used to access all Access Port (AHB-AP) registers.

# APACC/DPACC, when transferring data IN:
# Bits[34:3] = DATA[31:0]: 32bit data to transfer (write request)
# Bits[2:1] = A[3:2]: 2-bit address (debug/access port register)
# Bits[0:0] = RnW: Read request (1) or write request (0)
def data_in(instruction, bits):
    data, a, rnw = bits[:-3], bits[-3:-1], bits[-1]
    data_hex = '0x%x' % int('0b' + data, 2)
    r = 'Read request' if (rnw == '1') else 'Write request'
    # reg = dp_reg[a] if (instruction == 'DPACC') else apb_ap_reg[a]
    reg = dp_reg[a] if (instruction == 'DPACC') else a # TODO
    return 'New transaction: DATA: %s, A: %s, RnW: %s' % (data_hex, reg, r)

# APACC/DPACC, when transferring data OUT:
# Bits[34:3] = DATA[31:0]: 32bit data which is read (read request)
# Bits[2:0] = ACK[2:0]: 3-bit acknowledge
def data_out(bits):
    data, ack = bits[:-3], bits[-3:]
    data_hex = '0x%x' % int('0b' + data, 2)
    ack_meaning = ack_val.get(ack, 'Reserved')
    return 'Previous transaction result: DATA: %s, ACK: %s' \
           % (data_hex, ack_meaning)

def data_in_dtm(instruction, bits):
    data, a, rnw = bits[:-3], bits[-3:-1], bits[-1]
    data_hex = '0x%x' % int('0b' + data, 2)
    r = 'Read request' if (rnw == '1') else 'Write request'
    # reg = dp_reg[a] if (instruction == 'DPACC') else apb_ap_reg[a]
    reg = dp_reg[a] if (instruction == 'DPACC') else a # TODO
    return 'New transaction: DATA: %s, A: %s, RnW: %s' % (data_hex, reg, r)

class Decoder(srd.Decoder):
    api_version = 3
    id = 'jtag_riscv'
    name = 'JTAG / riscv'
    longname = 'Joint Test Action Group / RISC-V'
    desc = 'RISCV-specific JTAG protocol.'
    license = 'gplv2+'
    inputs = ['jtag']
    outputs = []
    tags = ['Debug/trace']
    annotations = (
        ('item', 'Item'),
        ('field', 'Field'),
        ('command', 'Command'),
        ('warning', 'Warning'),
        ('state', 'State'),
        ('di', 'Di'),
    )

    annotation_rows = (
        ('items', 'Items', (0,)),
        ('fields', 'Fields', (1,)),
        ('commands', 'Commands', (2,)),
        ('warnings', 'Warnings', (3,)),
        ('states', 'States', (4,)),
        ('dis', 'Dis', (5,)),
    )

    def __init__(self):
        self.reset()

    def reset(self):
        self.state = 'IDLE'
        self.laststate = 'None'
        self.samplenums = None
        self.instruction = None

    def start(self):
        self.out_ann = self.register(srd.OUTPUT_ANN)

    def putx(self, data):
        self.put(self.ss, self.es, self.out_ann, data)

    def putf(self, s, e, data):
        self.put(self.samplenums[s][0], self.samplenums[e][1], self.out_ann, data)

    def handle_reg_bypass(self, cmd, bits):
        self.putx([0, ['BYPASS: ' + bits]])

    def handle_reg_idcode(self, cmd, bits):
        bits = bits[1:]

        manuf, ver, part = decode_device_id_code(bits)
        cc = '0x%x' % int('0b' + bits[-12:-8], 2)
        ic = '0x%x' % int('0b' + bits[-7:-1], 2)
        manuf, ver, part = (int('0b' + manuf, 2),int('0b' + ver, 2),int('0b' + part, 2))

        self.putf(0, 0, [1, ['Reserved', 'Res', 'R']])
        self.putf(8, 11, [0, ['Continuation code: %s' % cc, 'CC', 'C']])
        self.putf(1, 7, [0, ['Identity code: %s' % ic, 'IC', 'I']])
        self.putf(1, 11, [1, ['Manufacturer: %x' % manuf, 'Manuf', 'M']])
        self.putf(12, 27, [1, ['Part: %x' % part, 'Part', 'P']])
        self.putf(28, 31, [1, ['Version: %x' % ver, 'Version', 'V']])

        #self.putf(32, 32, [1, ['BYPASS (BS TAP)', 'BS', 'B']])
        idcodestr = 'IDCODE: (%s: %s/%s)' % (manuf, ver, part)
        self.putx([2, [idcodestr]])

    def handle_reg_dpacc(self, cmd, bits):
        bits = bits[1:]
        s = data_in('DPACC', bits) if (cmd == 'DR TDI') else data_out(bits)
        self.putx([2, [s]])

    def handle_reg_apacc(self, cmd, bits):
        bits = bits[1:]
        s = data_in('APACC', bits) if (cmd == 'DR TDI') else data_out(bits)
        self.putx([2, [s]])

    def handle_reg_dmi(self, cmd, bits):
        if len(bits) > 34:
            if cmd == 'DR TDI':
                instruction = bits[-2:]
                data = bits[-34:-2]
                address = bits[0:-34]
                data_hex = '%s instr: 0x%X addr: 0x%X data: 0x%X' % (cmd, int('0b' + instruction, 2), int('0b' + address, 2), int('0b' + data, 2))
                #data_hex = '%s instr: 0x%x' % (cmd, int('0b' + instruction, 2))
                self.putx([2, [data_hex]])
            elif cmd  == 'DR TDO':
                instruction = bits[-1:0]
                data = bits[-33:-2]
                address = bits[0:-34]
                # data_hex = '%s instr: 0x%x addr: 0x%x data: 0x%x' % (cmd, int('0b' + instruction, 2), int('0b' + address, 2), int('0b' + data, 2))
                # self.putx([1, [data_hex]])
                data_hex = '%s 0x%x' % (cmd, int('0b' + bits, 2))
                self.putx([1, [data_hex]])
            elif cmd == 'IR TDO':
                self.putx([4, "IR TDO in dmi"])
            elif cmd == 'IR TDI':
                self.putx([3, "IR TDI in dmi"])   
            else:
                pass
        else:
            if cmd == 'DR TDI':
                data_hex = '%s 0x%x' % (cmd, int('0b' + bits, 2))
                self.putx([2, [data_hex]])
            elif cmd  == 'DR TDO':
                data_hex = '%s 0x%x' % (cmd, int('0b' + bits, 2))
                self.putx([1, [data_hex]])
            elif cmd == 'IR TDO':
                self.putx([4, "IR TDO in dmi"])
            elif cmd == 'IR TDI':
                self.putx([3, "IR TDI in dmi"])   
            else:
                pass

    def handle_reg_dtmcs(self, cmd, bits):
        # Read only registers
        version = bits[-4:]
        abits = bits[-10:-4]
        dmistat = bits[-12:-10]
        idle = bits[-15:-12]
        zero = bits[-16:-15]
        # Write only registers
        dmireset = bits[-17:-16]
        dmihardreset = bits[-18:-17]
        if cmd == 'IR TDO':
            # dmireset
            # dmihardreset
            # data_hex = '%s 0x%x' % (cmd, int('0b' + bits, 2))
            data_hex = 'dmireset = %s, dmihardreset = %s' % (dmireset, dmihardreset)
            self.putx([2, [data_hex]])        
        elif cmd == 'IR TDI':
            # version = bits[-4:]
            # abits = bits[-10:-4]
            # dmistat = bits[-12:-10]
            # idle = bits[-15:-12]
            # zero = bits[-16:-15]
            # data_hex = '%s 0x%x' % (cmd, int('0b' + bits, 2))
            data_hex = 'version = %s, abits = %s, dmistat = %s, idle = %s' % \
                (version, abits, dmistat, idle)
            self.putx([1, [data_hex]])  
        else:
            pass
            #data_hex = '%s bits: %s' % (cmd, bits)
            #self.putx([3, [data_hex]])  

    def handle_reset(self, cmd, bits):
        self.handle_reg_idcode(cmd, bits)
        # self.putx([1, [data_hex]])

    def handle_reg_abort(self, cmd, bits):
        bits = bits[1:]
        # Bits[31:1]: reserved. Bit[0]: DAPABORT.
        a = '' if (bits[0] == '1') else 'No '
        s = 'DAPABORT = %s: %sDAP abort generated' % (bits[0], a)
        self.putx([2, [s]])

        # Warn if DAPABORT[31:1] contains non-zero bits.
        if (bits[:-1] != ('0' * 31)):
            self.putx([3, ['WARNING: DAPABORT[31:1] reserved!']])

    def handle_reg_unknown(self, cmd, bits):
        bits = bits[1:]
        self.putx([2, ['Unknown instruction: %s' % bits]])

    def decode(self, ss, es, data):
        cmd, val = data

        self.ss, self.es = ss, es

        if self.state != self.laststate:
            self.putx([4, ['State = : %s' % self.state]])
            self.laststate = self.state

        if cmd != 'NEW STATE':
            # The right-most char in the 'val' bitstring is the LSB.
            val, self.samplenums = val
            self.samplenums.reverse()

        if cmd == 'IR TDI':
            # self.state = ir.get(val[:5], ['UNKNOWN', 0])[0]
            # self.putx([5, ['State after IR TDI = : %s' % self.state]])
            self.instruction = ir.get(val[:5], ['UNKNOWN', 0])[0]

        if cmd == 'NEW STATE':
            if val == 'TEST-LOGIC-RESET':
                self.state = 'RESET'
                return



        # State machine
        if self.state == 'RESET':
            if cmd != 'DR TDO':
                return
            self.handle_reset(cmd, val)
            self.state = 'IDLE'
        if self.state == 'BYPASS':
            # Here we're interested in incoming bits (TDI).
            if cmd != 'DR TDI':
                return
            self.handle_reg_bypass(cmd, val)
            self.state = 'IDLE'
        elif self.state in ('IDCODE', 'ABORT', 'UNKNOWN'):
            # Here we're interested in outgoing bits (TDO).
            if cmd != 'DR TDO':
                return
            handle_reg = getattr(self, 'handle_reg_%s' % self.state.lower())
            handle_reg(cmd, val)
            self.state = 'IDLE'
        elif self.state in ('DPACC', 'APACC'):
            # Here we're interested in incoming and outgoing bits (TDI/TDO).
            if cmd not in ('DR TDI', 'DR TDO'):
                return
            handle_reg = getattr(self, 'handle_reg_%s' % self.state.lower())
            handle_reg(cmd, val)
            if cmd == 'DR TDO': # Assumes 'DR TDI' comes before 'DR TDO'.
                self.state = 'IDLE'
        elif self.state in ('dtmcs', 'dmi'):
             # Here we're interested in incoming and outgoing bits (TDI/TDO).
            # if cmd not in ('DR TDI', 'DR TDO'):
            #     return
            handle_reg = getattr(self, 'handle_reg_%s' % self.state.lower())
            handle_reg(cmd, val)
            if cmd == 'DR TDO': # Assumes 'DR TDI' comes before 'DR TDO'.
                self.state = 'IDLE'
