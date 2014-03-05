#!/usr/bin/env python
# -*- coding: utf-8 -*-
###############################################################################
#
# msapy.py -- Modular Spectrum Analyzer Application Interface, in wxPython.
#
# The majority of this code is from spectrumanalyzer.bas, written by
# Scotty Sprowls and modified by Sam Wetterlin.
#
# Copyright (c) 2011, 2013 Scott Forbes
#
# This file may be distributed and/or modified under the terms of the
# GNU General Public License version 2 as published by the Free Software
# Foundation. (See COPYING.GPL for details.)
#
# This file is provided AS IS with NO WARRANTY OF ANY KIND, INCLUDING THE
# WARRANTY OF DESIGN, MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.
###############################################################################
from __future__ import division

##Updated with LB ver117rev0-A
##msaVersion$="117"   'ver117rev0
##msaRevision$="A"    'ver117a

version = "0.2.70_JGH Dec 23, 2013 PRELIM"
#released for editing as msapyP102 on 12/23/13
version = "0.2.71_EON Jan 10, 2014 PRELIM"
#released for editing as msapyP105 on 1/22/2014
version = "0.2.72 Jan 22, 2014 PRELIM"
version = "2.7.P3 (2/2/14)"
version = "2.7.P106 (2/3/14)"
version = "P108JGH_F (2/24/14)"
version = "P109GEORGE (2/25/14)"
version = "G109d (3/4/14)"
# NOTE by JGH Dec 8, 2013: An attempt has been made to convert the Python 2.7 code to Python 3.
# The conversion has been completed and affected print statements (require parentheses),
# lambda functions (require being enclosed in parentheses) and unicode encoding using chr().
# The print statements are now parenthesized. but the unicode and lambda are left as they were.

# This is the source for the MSAPy application. It's composed of two parts:
#
# Hardware Back End
#   * Communicates with the spectrum analyzer hardware.
#   * Has functions to initialize hardware, set modes, and capture data.
#
# GUI Front End
#   * Communicates with the user using the wxPython GUI library.
#   * Refreshes the spectrum graph on each timer tick whenever more capture data
#     from the back end is ready, or whenever its window is resized.
#
# The code is mostly a set of object-oriented classes, arranged starting with
# the most primitive; a search from the top will usually locate an item's
# definition. A search for "#=" will find the class definitions.
#
#
# TODO:
#   Reflection calibration.
#   More than 2 scales.
#   First scans of R-L scan mode disappear or go out-of-bounds
#   An "extrapolate ends" of cal table.
#   A "save" button in cal.

import sys
print ("Python:", sys.version) # Requires python v2.7
print("sys.platform: ", sys.platform)

isWin   = (sys.platform == "win32")
winUsesParallelPort = False # DO NOT TOUCH THIS LINE
isLinux=(sys.platform == "linux2")
isMac=(sys.platform=="darwin" or not (isWin or isLinux))

import msaGlobal
import os, re, string, subprocess
import time, thread, threading, traceback,  warnings
import array as uarray
import copy as dcopy
import numpy.version
import wx.grid #JGH 1/17/14
import wx.lib.colourselect as csel
import wx.lib.newevent as newevent
from wx.lib.dialogs import alertDialog, ScrolledMessageDialog
from numpy import angle, arange, array, cos, diff, floor, exp
from numpy import Inf, interp, isfinite, isnan, linspace, logspace, log10
from numpy import mean, mod, nan, nan_to_num, pi, poly1d, polyfit
from numpy import RankWarning, select, seterr, sin, sqrt, std, tan, zeros
from Queue import Queue
from StringIO import StringIO

msa = None
cb = None               # the current MSA Control Board, if present.
hardwarePresent = True  # True when cb represents actual hardware.

# Start EON Jan 22, 2014
usbSync = True
usbReadCount = 0
usbSyncCount = 20
incremental = True
logEvents = False
prt = False

RequiredFx2CodeVersion = "0.1"
CalVersion = "1.03" # compatible version of calibration files
showProfile = 0     # set to 1 to generate msa.profile. Then run showprof.py.
showThreadProfile = 0  # set to 1 to generate msa.profile for both threads

# debugging and profiling settings

debug = False        # set True to write debugging messages to msapy.log

# Graph update interval, in milliseconds. The tradoff is between smooth
# "cursor" movement and drawing overhead.
msPerUpdate = 100

# for raw magnitudes less than this the phase will not be read-- assumed
# to be noise
# goodPhaseMagThreshold = 0x2000
goodPhaseMagThreshold = 0x0000 # Scotty will determine usefulness of this 3/2/14

# Set to truncate S11 to the unity circle to ignore error due to S21
# measurement losses during calibrating
truncateS11ToUnity = False

# set numpy divide-by-zero errors to be fatal
##seterr(all="raise")

# appdir is the directory containing this program
appdir = os.path.abspath(os.path.dirname(sys.argv[0]))
resdir = appdir
if isWin:
    resdir = os.environ.get("_MEIPASS2")
    if not resdir:
        resdir = appdir
elif os.path.split(appdir)[1] == "Resources":
    appdir = os.path.normpath(appdir + "/../../..")

# standard font pointsize-- will be changed to calibrated size for this system
fontSize = 11

# set to disable auto-double-buffering and use of anti-aliased GraphicsContext
slowDisplay = isWin
print ("PROGRAM STARTED")

# Start EON Jan 10 2014
# globals for OSL calibration calculations

calWait = 50 # sweep wait during calibration # EON Jan 29, 2014

def message(message, caption="", style=wx.OK): # EON Jan 29, 2014
    dlg = wx.MessageDialog(msa.frame, message, caption, style)
    dlg.ShowModal()
    dlg.Destroy()
# End EON Jan 10 2014

#******************************************************************************
#****                          MSA Hardware Back End                      *****
#******************************************************************************

# Utilities.

# Convert to decibels.

def db(x):
    try:
        return 20. * log10(x)
    except FloatingPointError:
        print ("db(", x, ")?")
        raise

# Adjust degrees to be in range -180 to +180.

def modDegree(deg):
    return mod(deg + 180., 360.) - 180.

# Return an array of minimums/maximums between corresponding arrays a and b

def min2(a, b):
    return select([b < a], [b], default=a)

def max2(a, b):
    return select([b > a], [b], default=a)

# Convert string s to floating point, with an empty string returning 0.

def floatOrEmpty(s):
    if s == "":
        return 0.
    try:
        return float(s)
    except ValueError:
        ##print ("Bad float: '%s'" % s
        return 0.

# Convert an integer or float to a string, retaining precision

def gstr(n):
    return "%0.10g" % n

# Convert a value in MHz to a string with 1 Hz resolution

def mhzStr(mhz):
    return "%0.10g" % round(mhz, 6)

if 1:
    # Unicode-encoded special characters
    mu = u"\u03BC" # JGH mod4P3 -> mu = chr(956)
    Ohms = u"\u2126" #JGH mod4P3 -> Ohms = chr(8486)
    Infin = u"\u221E" # JGH mod4P3 -> Infin = chr(8734)
else:
    mu = "u"
    Ohms = "Ohms"
    Infin = "inf"

# SI-units prefixes, in order of power
SIPrefixes = ("f", "p", "n",  mu, "m", "", "k", "M", "G", "T", "Z")

# dictionary of the power-of-ten of each SI-unit prefix string
SIPowers = {}
i = -15
for pr in SIPrefixes:
    SIPowers[pr] = i
    i += 3

# useful units multipliers
fF = 1e-15
pF = pH = ps = 1e-12
nF = nH = ns = 1e-9
uF = uH = us = 1e-6
mOhm = mF = mH = mW = mV = 1e-3
kOhm = kHz = 1e3
MOhm = MHz = 1e6
GOhm = GHz = 1e9

# Add SI prefix to a string representation of a number.
# places:   significant digits.
# flags:    sum of any of:
SI_NO       = 1 # suppress SI units
SI_ASCII    = 2 # suppress Unicode

def si(n, places=5, flags=0):
    if n == None:
        return None
    if n == 0 or (flags & SI_NO):
        return "%%0.%gg" % places % round(n, places)
    if abs(n) == Inf:
        return ("-", "")[n > 0] + Infin
    try:
        thou = min(max(int((log10(abs(n)) + 15) / 3), 0), 9)
    except:
        thou = 0
    p = SIPrefixes[thou]
    if (flags & SI_ASCII) and p == mu:
        p = "u"
    return "%%0.%gg%%s" % places % (n * 10**(15-3*thou), p)

# Return the corresponding multiplier for an SI-unit prefix string.

def siScale(s):
    return 10**(SIPowers[s])

# Convert string s to floating point, with an empty string returning 0,
# String may be scaled by SI units.

numSIPat = r"([0-9.e\-+]+)([a-zA-Z]*)"

def floatSI(s):
    m = re.match(numSIPat, s)
    if m:
        try:
            sValue, units = m.groups()
            value = float(sValue)
            if len(units) > 0:
                p = units[0]
                if p in SIPrefixes:
                    value *= siScale(p)
                elif p == "u":
                    value *= siScale(mu)
                elif p == "K":
                    value *= siScale("k")
            return value
        except ValueError:
            ##print ("Bad float: '%s'" % s
            pass
    return 0.

# Convert frequencies between start,stop and center,span pairs, using
# a geometric mean if isLogF is True.

def StartStopToCentSpan(fStart, fStop, isLogF):
    if isLogF:
        fCent = sqrt(fStart * fStop)
    else:
        fCent = (fStart + fStop) / 2
    return fCent, fStop - fStart

def CentSpanToStartStop(fCent, fSpan, isLogF):
    if isLogF:
        # given span is always arithmetic, so convert it to geometric span
        # using quadradic formula
        r = fSpan / (2*fCent)
        fSpanG2 = r + sqrt(r**2 + 1)
        fStart = fCent / fSpanG2
        fStop = fCent * fSpanG2
    else:
        fSpan2 = fSpan / 2
        fStart = fCent - fSpan2
        fStop = fCent + fSpan2
    return fStart, fStop

# Divide without raising a divide-by-zero exception. Only used in register
# setup, where wrong values just give wrong frequencies.

def divSafe(a, b):
    if b != 0:
        return a / b;
    return a

#------------------------------------------------------------------------------
# Transform S21 data to equivalent S11.
# Returns S11, Z

def EquivS11FromS21(S21, isSeries, R0):
    save = seterr(all="ignore")
    if isSeries:
        # transform S21 back to series Zs
        Z = 2*R0*(1/S21 - 1)
    else:
        # transform S21 back to shunt Zsh
        Sinv = nan_to_num(1/S21)
        Z = R0/(2*(Sinv - 1))

    # then transform that to S11
    Z = nan_to_num(Z)
    S11 = nan_to_num((Z-R0) / (Z+R0))
    seterr(**save)
    if truncateS11ToUnity:
        S21 = select([abs(S11) > 1.], [exp(1j*angle(S11))], default=S11) # EON Jan 29, 2014
    return S11, Z

#------------------------------------------------------------------------------
# Debug-event gathering. These events are stored without affecting timing or
# the display, and may be dumped later via the menu Data>Debug Events.

eventNo = 0

class Event:
    def __init__(self, what):
        global eventNo
        self.what = what
        self.when = int(msElapsed())*1000 + eventNo
        if debug:
            print ("Event %5d.%3d: %s" % (self.when/1000, \
                mod(self.when, 1000), what))
        eventNo += 1

guiEvents = []

def ResetEvents():
    global eventNo, guiEvents
    eventNo = 0
    guiEvents = []

# Log one GUI event, given descriptive string. Records elapsed time.

def LogGUIEvent(what):
    global guiEvents
    if logEvents: # EON Jan 22, 2014
        guiEvents.append(Event(what))

#------------------------------------------------------------------------------
# Time delays that have higher resolution and are more reliable than time.sleep
# which is limited to OS ticks and may return sooner if another event occurs.

# Get current time in milliseconds relative to loading program, with typically
# 1 microsecond resolution.

if isWin:
    time0 = time.clock()

    def msElapsed():
        return (time.clock() - time0) * 1000

else:
    from datetime import datetime
    time0 = datetime.now()

    def msElapsed():
        dt = datetime.now() - time0
        return dt.seconds*1000 + dt.microseconds/1000

# Delay given number of milliseconds. May be over by the amount of an
# intervening task's time slice.

def msWait(ms):
    start = msElapsed()
    dt = 0
    while dt < ms:
        ##if (ms - dt) > 50:
        ##    # use sleep() for longer durations, as it saves power
        ##    time.sleep((ms - dt - 50)/1000)
        dt = msElapsed() - start

# Measure the mean and standard deviation of 100 time delays of given duration.
# The deviation will typically be large for small ms, due to the occasional
# relatively long delay introduced by the OS scheduling. The chance of that
# 'pause' hitting near the end of a longer delay is lower.

def meas(ms):
    ts = []
    t1 = msElapsed()
    for i in range(100):
        msWait(ms)
        t2 = msElapsed()
        ts.append(t2 - t1)
        t1 = t2
    return (mean(ts), std(ts), ts)

# Conditionally form a parallel circuit with elements in list.

def par2(a, b, isSeries=False):
    if isSeries:
        return a + b
    return (a*b) / (a+b)

def par3(a, b, c, isSeries=False):
    if isSeries:
        return a + b + c
    return (a*b*c) / (b*c + a*c + a*b)

# Convert a dictionary into a structure. Representation is evaluatable.

class Struct:
    def __init__(self, **entries):
        self.__dict__.update(entries)

    def __repr__(self):
        return "Struct(**dict(" + string.join(["%s=%s" % \
            (nm, repr(getattr(self, nm))) \
            for nm in dir(self) if nm[0] != "_"], ", ") + "))"

# Check that the file at path has one of the allowed extensions.
# Returns the path with extension added if necessary, or None if invalid.

def CheckExtension(path, parent, allowedExt, defaultExt=None):
    base, ext = os.path.splitext(path)
    if ext == "":
        if defaultExt:
            ext = defaultExt
        else:
            ext = allowedExt[0]
    elif not ext in allowedExt:
        alertDialog(parent, "Urecognized extension '%s'" % ext, "Error")
        return None
    return base + ext

# Check if a file exists at path and return True if not allowed to overwrite.

def ShouldntOverwrite(path, parent):
    if os.path.exists(path):
        dlg = wx.MessageDialog(parent,
            "A file with that name already exists. Overwrite it?",
            style=wx.ICON_EXCLAMATION|wx.YES_NO|wx.NO_DEFAULT)
        return dlg.ShowModal() != wx.ID_YES
    return False


#==============================================================================
# MSA Control Board.

class MSA_CB:
    # Port P1 bits and bitmasks
    P1_ClkBit = 0
    P1_PLL1DataBit = 1
    P1_DDS1DataBit = 2
    P1_PLL3DataBit = 3
    P1_DDS3DataBit = 4
    P1_PLL2DataBit = 4    # same bit as DDS3
    P1_FiltA0Bit = 5
    P1_FiltA1Bit = 6
    P1_Clk      = 1 << P1_ClkBit
    P1_PLL1Data = 1 << P1_PLL1DataBit
    P1_DDS1Data = 1 << P1_DDS1DataBit
    P1_PLL3Data = 1 << P1_PLL3DataBit
    P1_DDS3Data = 1 << P1_DDS3DataBit

    # P2 bits and bitmasks
    P2_le1   = 1 << 0  # LEPLL1
    P2_fqud1 = 1 << 1  # FQUD DDS1
    P2_le3   = 1 << 2  # LEPLL3
    P2_fqud3 = 1 << 3  # FQUD DDS3
    P2_le2   = 1 << 4  # LEPLL2
    P2_pdminvbit = 6   # INVERT PDM

    # P3 bits and bitmasks
    P3_ADCONV   = 1 << 7
    P3_ADSERCLK = 1 << 6
    P3_switchTR   = 5  # Trans/Refl switch
    P3_switchFR    = 4  # Fwd/Rev switch
    P3_switchPulse = 3  # Pulse
    P3_spare       = 2  # Spare
    P3_videoFiltV1 = 1  # Video filter V1, high bit
    P3_videoFiltV0 = 0  # Video filted V0, low bit

    # P4 bits and bitmasks
    P4_BandBit       = 0
    P4_Band1Bit1     = 1
    P4_Atten5Bit     = 2
    P4_AttenLEBit    = 3
    P4_AttenClkBit   = 4
    P4_AttenDataBit  = 5
    P4_AttenLE    = 1 << P4_AttenLEBit
    P4_AttenClk   = 1 << P4_AttenClkBit

    # P5 (status) bits and bitmasks
    P5_PhaseDataBit = 6   # from LPT-pin 10 (ACK)
    P5_MagDataBit   = 7   # from LPT-pin 11 (WAIT)
    P5_PhaseData = 1 << P5_PhaseDataBit
    P5_MagData   = 1 << P5_MagDataBit

    # default parallel 'control' port values
    contclear = 0x00    # take all LPT control lines low
    SELTINITSTRBAUTO = 0x0f  # take all high
    STRB      = 0x08    # take LPT-pin 1 high. (Strobe line, STRB)
    AUTO      = 0x04    # take LPT-pin 14 high. (Auto Feed line, AUTO)
    INIT      = 0x02    # take LPT-pin 16 high. (Init Printer line, INIT)
    SELT      = 0x01    # take LPT-pin 17 high. (Select In line, SELT)
    #                     P1    P2    P3    P4
    controlPortMap = (0, SELT, INIT, AUTO, STRB)

    show = False
    if debug:
        show = True   # JGH

    #--------------------------------------------------------------------------
    # Set the Control Board Port Px.

    def SetP(self, x, data):
        if self.show:
            print ("SetP%d 0x%02x" % (x, data))
        self.OutPort(data)
        self.OutControl(self.controlPortMap[x])
        self.OutControl(self.contclear)

    #--------------------------------------------------------------------------
    # Return Control Board data lines to idle state.

    def setIdle(self):
        self.OutPort(0)

    #--------------------------------------------------------------------------
    # Default interface: do nothing if no hardware present (for debugging UI).

    def OutPort(self, data):
        if self.show:
            print ("OutPort(0x%02x)" % data)

    def OutControl(self, data):
        if self.show:
            print ("OutControl(0x%02x)" % data)

    def InStatus(self):
        if self.show:
            print ("InStatus")
        return 0

    def Flush(self):
        if self.show:
            print ("Flush")
        pass

    def SendDevBytes(self, byteList, clkMask): # JGH 2/9/14
        if self.show:
            print ("SendDevBytes")
        pass

    def ReqReadADCs(self, n):
        if self.show:
            print ("ReadReqADCs")
        pass

    def GetADCs(self, n):
        if self.show:
            print ("GetADCs")
        pass

    # Delay given number of milliseconds before next output
    def msWait(self, ms):
        if self.show:
            print ("msWait")
        msWait(ms)

    def FlushRead(self):
        if self.show:
            print ("FlushRead")
        pass

    def HaveReadData(self):
        if self.show:
            print ("HaveReadData")
        return 0

    def Clear(self):
        if self.show:
            print ("Clear")
        pass

#==============================================================================
# Parallel port I/O interface.

if isWin and winUsesParallelPort: # THIS LINE IS ALWAYS FALSE AND THAT'S OK
    # Windows DLL for accessing parallel port
    from ctypes import windll
    try:
        windll.LoadLibrary(os.path.join(resdir, "inpout32.dll"))
    except WindowsError:
        # Start up an application just to show error dialog
        app = wx.App(redirect=False)
        app.MainLoop()
        dlg = ScrolledMessageDialog(None,
                        "\n  inpout32.dll not found", "Error")
        dlg.ShowModal()
        sys.exit(-1)
else:
    import usb
    if isMac:
        # OSX: tell ctypes that the libusb backend is located in the Frameworks directory
        fwdir = os.path.normpath(resdir + "/../Frameworks")
        print ("fwdir :    " + str(fwdir))
        if os.path.exists(fwdir):
            os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = fwdir

class MSA_CB_PC(MSA_CB):
    # standard parallel port addresses
    port = 0x378
    status = port + 1
    control = port + 2

    # parallel 'control' port values {~SELT, INIT, ~AUTO, ~STRB}
    contclear = 0x0b    # take all LPT control lines low
    SELTINITSTRBAUTO = 0x04  # take all high
    STRB      = 0x0a    # take LPT-pin 1 high. (Strobe line, STRB)
    AUTO      = 0x09    # take LPT-pin 14 high. (Auto Feed line, AUTO)
    INIT      = 0x0f    # take LPT-pin 16 high. (Init Printer line, INIT)
    SELT      = 0x03    # take LPT-pin 17 high. (Select In line, SELT)
    #                     P1    P2    P3    P4
    controlPortMap = (0, SELT, INIT, AUTO, STRB)

    def OutPort(self, data):
        windll.inpout32.Out32(self.port, data)

    def OutControl(self, data):
        windll.inpout32.Out32(self.control, data)

    def InStatus(self):
        return windll.inpout32.Inp32(self.status)

    # Send 40 bytes of PLL and DDC register data out port P1
    def SendDevBytes(self, byteList, clkMask): # JGH 2/9/14
        for byte in byteList: # JGH 2/9/14
            self.SetP(1, byte)             # data with clock low
            self.SetP(1, byte + clkMask)   # data with clock high

    # request a read of the ADCs, reading n bits
    def GetADCs(self, n):
        # take CVN high. Begins data conversion inside AtoD, and is completed
        # within 2.2 usec. keep CVN high for 3 port commands to assure full
        # AtoD conversion
        self.SetP(3, self.P3_ADCONV)
        # Status bit 15 of the serial data is valid and can be read at any time
        mag = phase = 0
        for i in range(n):
            self.SetP(3, self.P3_ADSERCLK) # CVN low and SCLK=1
            # read data, statX is an 8 bit word for the Status Port
            stat = self.InStatus()
            mag =   (mag   << 1) | (stat & self.P5_MagData)
            phase = (phase << 1) | (stat & self.P5_PhaseData)
            self.SetP(3, 0)          # SCLK=0, next bit is valid
        return (mag, phase)

#==============================================================================
# USBPAR interface module connected to MSA CB parallel port.
#
# 'control' port is FX2 port D
#   DB25 pins {1, 14, 16, 17} = FX2 port D [3:0] = {STRB, AUTO, INIT, SELT}
#   (to match Dave Roberts' hardware) This port includes the latched switches
# 'port' port is FX2 port B
#   DB25 pins {9:2} = FX2 port B [7:0]
# 'status' port is FX2 port A
#   DB25 pins {11, 10} = FX2 port A [5:4] = {WAIT, ACK}

class MSA_CB_USB(MSA_CB):
    # constants
    USB_IDVENDOR_CYPRESS = 0x04b4
    USB_IDPRODUCT_FX2 = 0x8613

    def __init__(self):
        self.show = debug
        self._wrCount = 0
        self._rdSeq = 0
        self._expRdSeq = 0
        self._writeFIFO = ""
        self._readFIFO = uarray.array('B', []) # JGH numpy raises its ugly head
        self._firstRead = True
        self.usbFX2 = None
        # self.min = 20

    # Look for the FX2 device on USB and initialize it and self.usbFX2 if found

    def FindInterface(self):
        if not self.usbFX2:
            for bus in usb.busses():
                for dev in bus.devices:
                    if dev.idVendor == self.USB_IDVENDOR_CYPRESS and dev.idProduct == self.USB_IDPRODUCT_FX2:
                        odev = dev.open()
                        if 1:
                        # Run prog to download code into the FX2
                        # Disable if the code is permanently loaded into the EPROM
                            try:
                                cycfx2progName = os.path.join(resdir, "cycfx2prog")
                                usbparName = os.path.join(resdir, "usbpar.ihx")
                                cmd = [cycfx2progName, "prg:%s" % usbparName, "run"]
                                if debug:
                                    print (" ".join(cmd))

                                p = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT,
                                            env=os.environ)

                                result = p.wait()  # JGH ??????????????

                                for line in p.stdout.readlines():
                                    print ("cycfx2prog:", line)
                            except OSError:
                                print ("Error: cycfx2prog:", sys.exc_info()[1].strerror)
                                return
                            if result != 0:
                                print ("cycfx2prog returned", result)
                                return
                            print ("CYPRESS DEVICE FOUND")
                            try:
                                odev = dev.open()

                                # --------------------------------------------------

    ##                            # If the program doesn't start, let it detach the
    ##                            # Kernel driver ONCE, and then comment out the line
    ##                            odev.detachKernelDriver(0)
    ##                            if debug:
    ##                               print ("Kernel Driver detached")
    ##                            odev.setConfiguration(1) # JGH 10/31/13
    ##                            if debug:
    ##                                print ("Configuration has been set")
    ##                            odev.releaseInterface() # JGH 10/14/13
    ##                            if debug:
    ##                                print ("Interface released")

                                # --------------------------------------------------

                                odev.claimInterface(0)
                                # Alt Interface 1 is the Bulk intf: claim device
                                odev.setAltInterface(1)
                                self.usbFX2 = odev
                                print ("")
                                print ("      **** FINISHED WITHOUT ERRORS ****")
                                print ("")
                            except usb.USBError:
                                print ("USBError Exception")
                                return

    # For debug only # JGH 1/25/14
    def ReadUSBdevices(self):
        # libusb-0.1 version: search USB devices for FX2

        for bus in usb.busses():
            for dev in bus.devices:
                if dev.idVendor == self.USB_IDVENDOR_CYPRESS and dev.idProduct == self.USB_IDPRODUCT_FX2:

                    if debug:
                        print (">>>>> CONFIGURATIONS:")
                        for cfg in dev:
                            print (">>>>> bConfigurationValue: ", cfg.bConfigurationValue, " <<<<<")
                            print (">>>>> bNumInterfaces: ", cfg.bNumInterfaces, " <<<<<")
                            print (">>>>> iConfiguration: ", cfg.iConfiguration, " <<<<<")
                            print (">>>>> bmAttributes: ", cfg.bmAttributes, " <<<<<")
                            print (">>>>> bMaxpower: ", cfg.bMaxPower, " <<<<<")
                            print ("")
                            print (">>>>> INTERFACES:")
                            for intf in cfg:
                                print (">>>>> bInterfaceNumber ", intf.bInterfaceNumber, " <<<<<")
                                print (">>>>> bAlternateSetting: ", intf.bAlternateSetting, " <<<<<")
                                print ("")
                                print (">>>>> END POINTS:")
                                for ep in intf:
                                    print (">>>>> bEndpointAddress: ", ep.bEndpointAddress, " <<<<<")
                                    print ("")

    # Send buffered write data to FX2
    def Flush(self):
        if debug:
            print (">>>894<<< MSA_CB_USB:Flush()", len(self._writeFIFO))
        if len(self._writeFIFO) > 0:
            fx2 = self.usbFX2
            if debug:
                print (">>>898<<< fx2:  " + str(fx2))
            fx2.bulkWrite(2, self._writeFIFO, 5000)
            self._writeFIFO = ""

    # Put write data to send (as a string) into the buffer
    def _write(self, data):
        if (len(self._writeFIFO) + len(data)) > 512:
            self.Flush()
        self._writeFIFO += data

    # Read any FX2 data, with a silent timout if none present
    def _read(self):
        fx2 = self.usbFX2
        try:
            data = fx2.bulkRead(0x86, 512, 1000)
            if self.show:
                print ("_read ->", string.join(["%02x" % b for b in data]))
        except usb.USBError:
            data = uarray.array('B', [])
            if self.show:
                print ("_read -> none")
        return data

    # Request a write of a byte to the data port
    def OutPort(self, byte):
        if self.show:
            print ("OutPort(0x%02x)" % byte)
        if debug:
            print ("MSA_CB_USB: OutPort at line 915")
        self._write("D" + chr(byte))

    # Request a write of a byte to the control port
    def OutControl(self, byte):
        if self.show:
            print ("OutControl(0x%02x)" % byte)
        if debug:
            print ("MSA_CB_USB: OutControl at line 845")
        self._write("C" + chr(byte))

    # Send 40 bytes of PLL and DDC register data out port P1
    def SendDevBytes(self, byteList, clkMask): # JGH 2/9/14
        s = string.join(map(chr, byteList), '')    # JGH 2/9/14
        if self.show:
            print ("SendDevBytes(clk=0x%02x, len=%d)" % (clkMask, len(s)))
        self._write("P" + chr(clkMask) + chr(len(s)) + s)

    # Request a delay given number of milliseconds before next output
    def msWait(self, ms):
        if self.show:
            print ("msWait(%d)" % ms)
        if type(ms) != type(1) or ms < 1:
            ##print ("msWait: bad value", ms)
            ms = 1
        if ms <= 255:
            self._write("W" + chr(ms))
        else:
            msWait(ms)

    # Request a flush of the read buffer in the FX2
    def FlushRead(self):
        if self.show:
            print ("FlushRead()")
        self._write("F" + chr(0))

    # Check for read data waiting in the FX2, returning the num of bytes read
    def HaveReadData(self):
        if self.show:
            print ("HaveReadData start")
        r = self._read()
        if self.show:
            print ("read:", r)
        if not isMac:
            r = uarray.array('B', r)
        self._readFIFO += r
        if self.show:
            print ("HaveReadData ->", len(self._readFIFO))
        return len(self._readFIFO)

    # Get the requested read-status data byte
    def InStatus(self):
        if self.show:
            print ("InStatus start")
        retry = 5
        while len(self._readFIFO) < 1:
            r = self._read()
            if not isMac:
                r = uarray.array('B', r)
            self._readFIFO += r
            if --retry == 0:
                break
        #if retry < self.min:
        #   self.min = retry
        #    print ("retry %d" % retry)
        if len(self._readFIFO) < 1:
            print ("InStatus: no data")
            return 0
        # put {WAIT, ACK} in bits [7:6]
        # result = ((self._readFIFO[0] << 2) & 0xc0) ^ 0x80
        result = self._readFIFO[0]
        self._readFIFO = self._readFIFO[1:]
        if self.show:
            print ("InStatus -> 0x%02x" % result)
        return result

    # Request a read of the ADCs, reading n bits
    def ReqReadADCs(self, n):
        if self.show:
            print ("ReqReadADCs(%d)" % n)
        self._write("A" + chr(n))

    # Return the data previously read from the ADCs
    def GetADCs(self, n):
        global usbSync, usbReadCount, usbSyncCount
        mag = phase = 0
        tmp = 16
        for i in range(n):
            stat = self.InStatus()   # read data
            if usbSync:
                usbReadCount += 1
                err = False
                if i == 0:
                    if ((stat & 0xf) != 0xf):
                        print ("%10d out of sync %x" % (usbReadCount, stat))
                        err = True
                else:
                    if (stat & 0xf) != (tmp & 0x7):
                        print ("%10d out of sync %2d %2d %02x" % (usbReadCount, i, tmp, stat))
                        err = True
                    tmp -= 1;
                if err:
                    usbSyncCount -= 1
                    if usbSyncCount < 0:
                        usbSync = False
            stat = ((stat << 2) & 0xff) ^ 0x80
            mag =   (mag   << 1) | (stat & self.P5_MagData)
            phase = (phase << 1) | (stat & self.P5_PhaseData)
        if self.show:
            print ("GetADCs(%d) -> " % n, mag, phase)
        return (mag, phase)

    # Check that the FX2 is loaded with the proper version of code
    def ValidVersion(self):
        self._write("V" + chr(0))
        self.FlushRead()
        self.Flush()
        msWait(100)
        fx2Vers = None
        if self.HaveReadData() >= 2:
            fx2Vers = "%d.%d" % tuple(self._readFIFO[:2])
            if self.show:
                print (">>>1018<<< fx2Vers: " + str(fx2Vers))
            self._readFIFO = self._readFIFO[2:]
        if self.show:
            print ("ValidVersion ->", fx2Vers)
        if fx2Vers != RequiredFx2CodeVersion:
            print (">>>1023<<< Wrong FX2 code loaded: ", \
                   fx2Vers, " need: ", RequiredFx2CodeVersion)
            return False
        else:
            return True

    # Clear the read and write buffers and counts
    def Clear(self):
        self.FindInterface()
        self.FlushRead()
        self.Flush()
        # this clears out any FIFOed reads, but also causes a timeout
        ##if self._firstRead:
        ##    self._read()
        ##    self._firstRead = False
        self._wrCount = 0
        self._rdSeq = 0
        self._expRdSeq = 0
        self._writeFIFO = ""
        self._readFIFO =  uarray.array('B', [])

cb = None               # the current MSA Control Board, if present.
hardwarePresent = True  # True when cb represents actual hardware.
#==============================================================================
class MSA_RPI(MSA_CB):
    # constants
    
    def __init__(self):
        self.show = debug
        text = "This interface has not been implemented yet"
        message(text, caption="RPI Error", style=wx.OK)

#==============================================================================
class MSA_BBB(MSA_CB):
    # constants
    
    def __init__(self):
        self.show = debug
        text = "This interface has not been implemented yet"
        message(text, caption="BBB Error", style=wx.OK)

#==============================================================================
# An MSA Local Oscillator DDS and PLL.

class MSA_LO:
    # def __init__(self, id, freq, pllBit, le, fqud, PLLphasefreq, phasepolarity, appxdds, ddsfilbw):
    # JGH Above line substituted by the following
    def __init__(self, loid, freq, pllBit, le, fqud, PLLphasefreq, phasepolarity, \
                 appxdds, ddsfilbw, PLLtype): # JGH 2/7/14 Fractional mode not used
        self.id = loid                        # LO number, 1-3
        self.freq = freq                    # LO frequency
        self.CBP1_PLLDataBit = pllBit       # port 1 bit number for PLL data
        self.CBP2_LE = le                   # port 2 mask for Latch Enable line
        self.CBP2_FQUD = fqud               # port 2 mask for FQUD line
        self.PLLphasefreq = PLLphasefreq    # Approx. Phase Detector Frequency for PLL.
                # Use .974 when DDS filter is 15 KHz wide.
                # PLLphasefreq must be less than the following formula:
                # PLLphasefreq < (VCO 1 minimum frequency) x self.ddsfilbw/appxdds
        self.phasepolarity = phasepolarity
        self.appxdds = appxdds              # nominal DDS output frequency, to steer the PLL.
                # (Near 10.7 MHz). appxdds must be the center freq. of DDS xtal filter;
                # Exact value determined in calibration.
        self.ddsfilbw = ddsfilbw            # DDS xtal filter bandwidth (in MHz), at the 3 dB points.
                #Usually 15 KHz.
        self.PLLtype = PLLtype              # JGH COMMENT: PLLtype not needed here?
        
        self.ddsoutput = 0.                 # actual output of DDS (input Ref to PLL)
        self.ncounter = 0.                  # PLL N counter
        self.Acounter = 0.                  # PLL A counter
        self.Bcounter = 0.                  # PLL B counter
        self.fcounter = 0.                  # PLL fractional-mode N counter
        if debug:
            print ("LO%d init: PDF=%f" % (id, PLLphasefreq))

        # PLL R counter
        self.rcounter = int(round(divSafe(self.appxdds, self.PLLphasefreq)))
##        if msa.spurcheck and not self.PLLmode:  # JGH 2/7/14 Fractional mode not used
        if msa.spurcheck:
            self.rcounter += 1  # only do this for IntegerN PLL

        self.pdf = 0        # phase detector frequency of PLL (MHz)


    #--------------------------------------------------------------------------
    # Create rcounter, pdf.

    def CreateRcounter(self, reference):
        self.rcounter = int(round(divSafe(reference, self.PLLphasefreq)))
        self.pdf = divSafe(reference, self.rcounter)
        if debug:
            print ("LO%d: R=%d=0x%06x pdf=%f" % (self.id, self.rcounter, \
                        self.rcounter, self.pdf))
        return self.rcounter, self.pdf # JGH 2/1/14

    #--------------------------------------------------------------------------
    # Set a PLL's register.

    def CommandPLL(self, data):
        # CommandPLLslim --
        if debug:
            print ("LO%d CommandPLL 0x%06x" % (self.id, data))
        shift = 23 - self.CBP1_PLLDataBit
        mask = 1 << 23
        # shift data out, MSB first
        for i in range(24):
##            a = ((data & mask) >> shift) + msa.bitsRBW # JGH Use next line
            a = ((data & mask) >> shift)
            cb.SetP(1, a)                  # data with clock low
            cb.SetP(1, a + cb.P1_Clk)      # data with clock high
            # shift next bit into position
            data <<= 1
        # remove data, leaving bitsRBW data to filter bank.
##        cb.SetP(1, msa.bitsRBW) # JGH use next line
        cb.SetP(1, 0)

        # send LEs to PLL1, PLL3, FQUDs to DDS1, DDS3, and command PDM
        # begin by setting up init word=LEs and Fquds + PDM state for thisstep
        pdmcmd = msa.invPhase << cb.P2_pdminvbit
        cb.SetP(2, self.CBP2_LE + pdmcmd) # present data to buffer input
        # remove the added latch signal to PDM, leaving just the static data
        cb.SetP(2, pdmcmd)
        cb.setIdle()

    #--------------------------------------------------------------------------
    # Initialize the PLL's R register.

    def CommandPLLR(self): #JGH added additional PLL types

        if self.PLLtype == "2325":
            # N15 = 1 if preselector = 32, = 0 for preselctor = 64, default 1
            self.CommandPLL((self.rcounter << 1) + 0x1 + (0x1 << 15))

        # Command2326R --
        if (self.PLLtype == "2326" or self.PLLtype == "4118"):
            self.CommandPLL((self.phasepolarity << 7) + 0x3)
            self.CommandPLL(self.rcounter << 2)

        if self.PLLtype == "2350":
            self.CommandPLL((0X123 << 6) + (0x61 << 17))
            self.CommandPLL(0x1 + (0x1 << 14) + (0x1 << 18) + (0x1 << 22))
            self.CommandPLL(0x2 + (self.rcounter << 2) + (0x15 << 18))

        if self.PLLtype == "2353":
            self.CommandPLL((0x1 << 22))
            self.CommandPLL((0x1))
            #N23 Fractional mode, delay line 0= slow,1 =fast
            self.CommandPLL(0x2 + (self.rcounter << 2) + (self.phasepolarity << 17)
                             + (0x15 << 18))

        if (self.PLLtype == "4112" or self.PLLtype == "4113"):
            # If preselector = 8 then N22=0, N23=0
            # If preselector =16 then N22=1, N23=0
            # If preselector =32 then N22=0, N23=1 , default 32
            # if preselector =64 then N22=1, N23=1
            self.CommandPLL((self.phasepolarity << 7) + 0x3 + (0x1 << 15)
                            + (0x1 << 18) + (0x1 << 23))
            self.CommandPLL((self.rcounter << 2) + (0x1 << 22))

    #--------------------------------------------------------------------------
    # Reset serial DDS without disturbing Filter Bank or PDM.

    def ResetDDSserSLIM(self):
        # must have DDS (AD9850/9851) hard wired. pin2=D2=0, pin3=D1=1,
        # pin4=D0=1, D3-D7 are don# t care. this will reset DDS into
        # parallel, invoke serial mode, then command to 0 Hz.
        if debug:
            print ("XXXXX 996, ResetDDSserSLIM XXXXX")
        pdmcmd = msa.invPhase << cb.P2_pdminvbit
        #bitsRBW = msa.bitsRBW

        # (reset DDS1 to parallel) WCLK up, WCLK up and FQUD up, WCLK up and
        # FQUD down, WCLK down
        # apply last known filter path and WCLK=D0=1 to buffer
##        cb.SetP(1, bitsRBW + cb.P1_Clk) # JGH use next line instead
        cb.SetP(1, cb.P1_Clk)
        # apply last known pdmcmd and FQUD=D3=1 to buffer
        cb.OutPort(pdmcmd + self.CBP2_FQUD)
        # DDSpin8, FQUD up,DDS resets to parallel,register pointer will reset
        cb.OutControl(cb.INIT)
        # DDSpin8, FQUD down
        cb.OutPort(pdmcmd)
        # disable buffer, leaving last known PDM state latched
        cb.OutControl(cb.contclear)
        # apply last known filter path and WCLK=D0=0 to buffer
##        cb.SetP(1, bitsRBW) # JGH Use next line instead
        cb.SetP(1, 0)
        # (invoke serial mode DDS1)WCLK up, WCLK down, FQUD up, FQUD down
        # apply last known filter path and WCLK=D0=1 to buffer
##        cb.OutPort(bitsRBW + cb.P1_Clk) # JGH Use next line instead
        cb.OutPort(cb.P1_Clk)
        # DDSpin9, WCLK up to DDS
        cb.OutControl(cb.SELT)
        # apply last known filter path and WCLK=D0=0 to DDS
##        cb.OutPort(bitsRBW) # JGH Use next line instead
        cb.OutPort(0)
        # disable buffer, leaving bitsRBW
        cb.OutControl(cb.contclear)
        # apply last known pdmcmd and FQUD=D3=1 to buffer
        cb.OutPort(pdmcmd + self.CBP2_FQUD)
        # DDSpin8, FQUD up,DDS resets to parallel,register pointer will reset
        cb.OutControl(cb.INIT)
        # DDSpin8, FQUD down
        cb.OutPort(pdmcmd)
        # disable buffer, leaving last known PDM state latched
        cb.OutControl(cb.contclear)

        # (flush and command DDS1) D7, WCLK up, WCLK down, (repeat39more),
        # FQUD up, FQUD down present data to buffer,latch buffer,disable
        # buffer, present data+clk to buffer,latch buffer,disable buffer

        # JGH the following block, changed to the next below
##        a = bitsRBW
##        for i in range(40):
##            # data with clock low
##            cb.SetP(1, a)
##            # data with clock high
##            cb.SetP(1, a + cb.P1_Clk)
##        # leaving bitsRBW latched
##        cb.SetP(1, a)

        #a = 0
        for i in range(40):
            # data with clock low
            cb.SetP(1, 0)
            # data with clock high
            cb.SetP(1, cb.P1_Clk)
        # leaving bitsRBW latched
        cb.SetP(1, 0)

        # apply last known pdmcmd and FQUD=D3=1 to buffer
        cb.OutPort(pdmcmd + self.CBP2_FQUD)
        # DDSpin8, FQUD up,DDS resets to parallel,register pointer will reset
        cb.OutControl(cb.INIT)
        # DDSpin8, FQUD down
        cb.OutPort(pdmcmd)
        # disable buffer, leaving last known PDM state latched
        cb.OutControl(cb.contclear)
        if debug:
            print ("ResetDDSserSLIM done")

    #--------------------------------------------------------------------------
    # Create Fractional Mode N counter.

    def _CreateFractionalNcounter(self, appxVCO, reference):
        # approximates the Ncounter for PLL
        ncount = divSafe(appxVCO, (reference/self.rcounter))
        self.ncounter = int(ncount)
        fcount = ncount - self.ncounter # EON Jan 29, 2014
        self.fcounter = int(round(fcount*16))
        if self.fcounter == 16:
            self.ncounter += 1
            self.fcounter = 0
        # actual phase freq of PLL
        self.pdf = divSafe(appxVCO, (self.ncounter + (self.fcounter/16)))

    #--------------------------------------------------------------------------
    # Create Integer Mode N counter.

    def CreateIntegerNcounter(self, appxVCO, reference):
        # approximates the Ncounter for PLL
        ncount = divSafe(appxVCO, divSafe(reference, self.rcounter))
        self.ncounter = int(round(ncount))
        if debug:
            print(">>>1345<<< appxVCO, reference, ncounter: ", \
                  appxVCO, reference, self.ncounter)
        self.fcounter = 0
        # actual phase freq of PLL
        #self.pdf = divSafe(appxVCO, self.ncounter) # JGH 2/2/14 Beware of globals!

    #--------------------------------------------------------------------------
    # Create PLL N register.

    def CreatePLLN(self):

##        self.preselector = (32, 16)[self.PLLmode] # JGH 2/7/14 PLLmode not used
        self.preselector = 32
        fcounter = 0 # EON Jan 29, 2014

        # CreateNBuffer,
        PLLN = self.PLLtype  # JGH added

        Bcounter = int(self.ncounter/self.preselector)
        Acounter = int(self.ncounter-(Bcounter*self.preselector))

        if debug:
            print(">>>1364<<< PLLN: ", PLLN)
            print("ncounter: ", self.ncounter)
            print ("LO%d: Acounter=" % self.id, Acounter, "Bcounter=", Bcounter)

        if PLLN == "2325":
            if Bcounter < 3:
                raise RuntimeError(PLLN + "Bcounter <3")
            if Bcounter > 2047:
                raise RuntimeError(PLLN + "Bcounter > 2047")
            if Bcounter < Acounter:
                raise RuntimeError(PLLN + "Bcounter<Acounter")
            Nreg = (Bcounter << 8) + (Acounter << 1)

        if (PLLN == "2326" or PLLN == "4118"):
            if Bcounter < 3:
                raise RuntimeError(PLLN + "Bcounter <3")  # JGH Error < 3 common to all
            if Bcounter > 8191:
                raise RuntimeError(PLLN + "Bcounter >8191")
            if Bcounter < Acounter:
                raise RuntimeError(PLLN + "Bcounter<Acounter")
            # N20 is Phase Det Current, 1= 1 ma (add 1 << 20), 0= 250 ua
            Nreg = 1 + (1 << 20) + (Bcounter << 7) + (Acounter << 2)

        if PLLN == "2350":
            if Bcounter < 3:
                raise RuntimeError(PLLN + "Bcounter <3")  # JGH Error < 3 common to all
            if Bcounter > 1023:
                raise RuntimeError(PLLN + "Bcounter > 2047")
            if Bcounter < Acounter + 2:
                raise RuntimeError(PLLN + "Bcounter<Acounter")
            # N21: 0 if preselector = 16 else if preselector =32 then = 1 and add (1 << 21)
            Nreg = 3 + (Bcounter << 11) + (Acounter << 6) + (fcounter << 2)

        if PLLN == "2353":
            if Bcounter < 3:
                raise RuntimeError(PLLN + "Bcounter <3")  # JGH Error < 3 common to all
            if Bcounter > 1023:
                raise RuntimeError(PLLN + "Bcounter > 2047") # EON Jan 29, 2014
            if Bcounter < Acounter + 2:
                raise RuntimeError(PLLN + "Bcounter<Acounter")
            # N21: 0 if preselector = 16 else if preselector =32 then = 1 and add (1 << 21)
            Nreg = (3 + (Bcounter << 11) + (Acounter << 6) + (fcounter << 2))

        if (PLLN == "4112" or PLLN == "4113"):
            if Bcounter < 3:
                raise RuntimeError(PLLN + "Bcounter <3")  # JGH Error < 3 common to all
            if Bcounter > 8191:
                raise RuntimeError(PLLN + "Bcounter > 2047")
            if Bcounter < Acounter:
                raise RuntimeError(PLLN + "Bcounter<Acounter")
            # N21:  0=Chargepump setting 1; 1=setting 2; default 0
            Nreg = 1 + (Bcounter << 8) + (Acounter << 2)

        self.PLLbits = Nreg
        self.Acounter = Acounter
        self.Bcounter = Bcounter

        if debug:
            print ("LO%d: N= 0x%06x" % (self.id, Nreg))
            print("PLLbits(Nreg), Acounter, Bcounter: ", self.PLLbits, self.Acounter, self.Bcounter)

    #--------------------------------------------------------------------------
    # Calculate PLL and DDS settings for given frequency.

    def Calculate(self, freq):
        self.freq = freq
        appxVCO = freq
        reference = self.appxdds
        if debug:
            print ("LO%d: freq=" % self.id, freq, "ref=", reference, \
                "rcounter=", self.rcounter)

##        if self.PLLmode: # PLLmode not used, always Integer
##            self._CreateFractionalNcounter(appxVCO, reference)
##        else:
##            self.CreateIntegerNcounter(appxVCO, reference)
##            self.pdf = divSafe(appxVCO, self.ncounter) # JGH 2/2/14
        # JGH 2/7/14
        self.CreateIntegerNcounter(appxVCO, reference)
        self.pdf = divSafe(appxVCO, self.ncounter) # JGH 2/2/14
        # JGH 2/7/14

        if debug:
            print ("LO%d: ncounter=" % self.id, self.ncounter, "fcounter=", \
                self.fcounter, "pdf=", self.pdf)

        # actual output of DDS (input Ref to PLL)
        self.ddsoutput = self.pdf * self.rcounter

# JGH 2/7/14 starts: PLLmode not used, always Integer
##        if self.PLLmode:
##            # AutoSpur-- used only in MSA when PLL is Fractional
##            # reset spur, and determine if there is potential for a spur
##            spur = 0
##            LO2freq = msa.LO2freq
##            finalfreq = msa.finalfreq
##            firstif = LO2freq - finalfreq
##            # fractional frequency
##            ff = divSafe(self.ddsoutput, (self.rcounter*16))
##            if ff != 0:
##                harnonicb = int(round(firstif / ff))
##                harnonica = harnonicb - 1
##                harnonicc = harnonicb + 1
##                firstiflow = LO2freq - (finalfreq + msa.finalbw/1000)
##                firstifhigh = LO2freq - (finalfreq - msa.finalbw/1000)
##                if (harnonica*ff > firstiflow and \
##                    harnonica*ff < firstifhigh) or \
##                   (harnonicb*ff > firstiflow and \
##                    harnonicb*ff < firstifhigh) or \
##                   (harnonicc*ff > firstiflow and \
##                    harnonicc*ff < firstifhigh):
##                    spur = 1
##                    if self.ddsoutput < self.appxdds:
##                        self.fcounter -= 1
##                    elif self.ddsoutput > self.appxdds:
##                        self.fcounter += 1
##                if self.fcounter == 16:
##                    self.ncounter += 1
##                    self.fcounter = 0
##                elif self.fcounter < 0:
##                    self.ncounter -= 1
##                    self.fcounter = 15
##                self.pdf = divSafe(self.freq, (self.ncounter + \
##                    (self.fcounter/16)))
##                # actual output of DDS (input Ref to PLL)
##                self.ddsoutput = self.pdf * self.rcounter
##                if debug:
##                    print ("LO%d: AutoSpur ddsoutput=" % self.id, \
##                        self.ddsoutput, "pdf=", self.pdf)
##
##            # ManSpur -- used only in MSA when PLL is Fractional
##            #            and Spur Test button On
##            if msa.spurcheck:
##                if self.ddsoutput < self.appxdds:
##                    # causes +shift in pdf
##                    self.fcounter -= 1
##                elif self.ddsoutput > self.appxdds:
##                    # causes -shift in pdf
##                    self.fcounter += 1
##            if self.fcounter == 16:
##                self.ncounter += 1
##                self.fcounter = 0
##            elif self.fcounter < 0:
##                self.ncounter -= 1
##                self.fcounter = 15
##            self.pdf = divSafe(self.freq, (self.ncounter + (self.fcounter/16)))
##            # actual output of DDS (input Ref to PLL)
##            self.ddsoutput = self.pdf * self.rcounter
##            if debug:
##                print ("LO%d: ManSpur ddsoutput=" % self.id, self.ddsoutput, \
##                        "pdf=", self.pdf)
# JGH 2/7/14 ends

        self.CreatePLLN()

        # CalculateThisStepDDS1 --
        # JGH 2/2/14
        if abs(self.ddsoutput-self.appxdds) > self.ddsfilbw/2:
            raise RuntimeError("DDS%doutput outside filter range: output=%g "\
                               "pdf=%g" % (self.id, self.ddsoutput, self.pdf))


        #CreateBaseForDDSarray --

        # The formula for the frequency output of the DDS
        # (AD9850, 9851, or any 32 bit DDS) is taken from:
        # ddsoutput = base*msa.masterclock/2^32
        # rounded off to the nearest whole bit
        base = int(round(divSafe(self.ddsoutput * (1<<32), msa.masterclock))) # JGH 2/2/14
        self.DDSbits = base
        if debug:
            print ("LO%d: base=%f=0x%x" % (self.id, base, base))


#==============================================================================
# Holder of the parameters and results of one scan.

class Spectrum:
    def __init__(self, when, pathNo, fStart, fStop, nSteps, Fmhz):
        # Start EON Jan 10 2014
        self.isLogF = (Fmhz[0] + Fmhz[2])/2 != Fmhz[1]
        self.desc = "%s, Path %d, %d %s steps, %g to %g MHz." % \
            (when, pathNo, nSteps, ("linear", "log")[self.isLogF], fStart, fStop)
        # End EON Jan 10 2014
        self.nSteps = nSteps        # number of steps in scan
        self.Fmhz = Fmhz            # array of frequencies (MHz), one per step
        n = nSteps + 1
        self.oslCal = False        # EON Jan 10 2014
        self.Sdb = zeros(n)         # array of corresponding magnitudes (dB)
        self.Sdeg = zeros(n)        # phases (degrees)
        self.Scdeg = zeros(n)       # continuous phases (degrees)
        self.Mdb = zeros(n)         # raw magnitudes (dB)
        self.Mdeg = zeros(n)        # raw phases (degrees)
        self.magdata = zeros(n)     # magnitude data from ADC
        self.phasedata = zeros(n)   # phase data from ADC
        self.Tread = zeros(n)       # times when captured (ms from start)
        self.step = 0               # current step number
        self.vaType = None
        self.trva = None
        self.vbType = None
        self.trvb = None
        LogGUIEvent("Spectrum n=%d" % n)

    # Set values on step i in the spectrum. Returns True if last step.

    def SetStep(self, valueSet):
        i, Sdb, Sdeg, Scdeg, magdata, phasedata, Mdb, Mdeg, Tread = valueSet
        if i <= self.nSteps:
            self.step = i
            LogGUIEvent("SetStep %d, len(Sdb)=%d" % (i, len(self.Sdb)))
            self.Sdb[i] = Sdb
            self.Sdeg[i] = Sdeg
            self.Scdeg[i] = Scdeg
            self.Mdb[i] = Mdb
            self.Mdeg[i] = Mdeg
            self.magdata[i] = magdata
            self.phasedata[i] = phasedata
            self.Tread[i] = Tread
            if self.trva:
                self.trva.SetStep(self, i)
            if self.trvb:
                self.trvb.SetStep(self, i)
        return i == self.nSteps

    # Spectrum[i] returns the tuple (Fmhz, Sdb, Sdeg) for step i
    def __getitem__(self, i):
        return self.Fmhz[i], self.Sdb[i], self.Sdeg[i]

    #--------------------------------------------------------------------------
    # Write spectrum and input data to a text file.

    def WriteInput(self, fileName, p):
        f = open(fileName, "w")
        f.write( \
            " Step           Calc Mag  Mag A/D  Freq Cal Processed Pha A/D\n")
        f.write( \
            " Num  Freq (MHz)  Input   Bit Val   Factor    Phase   Bit Val\n")

        for i in range(len(self.Fmhz)):
            f.write("%4d %11.6f %8.3f %6d %9.3f %8.2f %8d\n" %\
                        (i, self.Fmhz[i], self.Sdb[i], self.magdata[i],
                        0., self.Sdeg[i], self.phasedata[i]))
        f.close()

    #--------------------------------------------------------------------------
    # Write spectrum to an S1P-format file.

    def WriteS1P(self, fileName, p, contPhase=False):
        f = open(fileName, "w")
        f.write("!MSA, msapy %s\n" % version)
        f.write("!Date: %s\n" % time.ctime())
        f.write("!%s Sweep Path %d\n" % \
            (("Linear", "Log")[p.isLogF], p.indexRBWSel+1))
        f.write("# MHz S DB R 50\n")
        f.write("!  MHz       S21_dB    S21_Deg\n")
        Sdeg = self.Sdeg
        if contPhase:
            Sdeg = self.Scdeg
        Sdeg = select([isnan(Sdeg)], [0], default=Sdeg)
        for freq, Sdb, Sdeg in zip(self.Fmhz, self.Sdb, Sdeg):
            f.write("%11.6f %10.5f %7.2f\n" % \
                    (freq, Sdb, Sdeg))
        f.close()

    #--------------------------------------------------------------------------
    # Read spectrum from an S1P file. Constructs the Spectrum too.

    @classmethod
    def FromS1PFile(cls, fileName):
        fScale = 1.
        R0 = 50
        Fmhz = []
        Sdb = []
        Sdeg = []
        when = "**UNKNOWN DATE**"
        pathNo = 1
        f = open(fileName, "r")

        for line in f.readlines():
            line = line.strip()
            if len(line) > 1:
                if line[0] == "!":
                    if line[1:6] == "Date:":
                        when = line[6:].strip()
                elif line[0] == "#":
                    words = string.split(line[1:])
                    i = 0
                    while i < len(words):
                        word = words[i]
                        i += 1
                        if len(word) > 1 and word[-2:] == "Hz":
                            fScale = siScale(word[:-2]) / MHz
                        elif word == "S":
                            sType = words[i]
                            if sType != "DB":
                                raise ValueError( \
                                    "Unsupported S type '%s' % sType")
                            i += 1
                        elif word == "R":
                            R0 = words[i]
                            i += 1
                        else:
                            raise KeyError("Unrecognized S1P keyword '%s'" \
                                            % word)
                else:
                    words = string.split(line)
                    if len(words) != 3:
                    # Start EON Jan 22, 2014
                        f.close()
                        return None
##                        raise ValueError( \
##                            "S1P file format wrong: expected freq, Sdb, Sdeg")
                    # End EON Jan 22, 2014
                    Fmhz.append(float(words[0]) * fScale)
                    Sdb.append(float(words[1]))
                    Sdeg.append(float(words[2]))
        f.close()

        n = len(Fmhz)
        if n == 0:
            # Start EON Jan 22, 2014
            return None
##            raise ValueError("S1P file: no data found")
            # End EON Jan 22, 2014

        print ("Read %d steps." % (n-1), "Start=", Fmhz[0], "Stop=", Fmhz[-1])
        this = cls(when, pathNo, Fmhz[0], Fmhz[-1], n - 1, array(Fmhz))
        this.Sdb = array(Sdb)
        this.Sdeg = array(Sdeg)
        this.Scdeg = this.Sdeg
        return this

# Create a new Event class and EVT binder function
(UpdateGraphEvent, EVT_UPDATE_GRAPH) = newevent.NewEvent()

#==============================================================================
# Modular Spectrum Analyzer.

class MSA:
    # Major operating modes
    MODE_SA = 0
    MODE_SATG = 1
    MODE_VNATran = 2
    MODE_VNARefl = 3
    modeNames = ("Spectrum Analyzer", "Spectrum Analyzer with TG",
                 "VNA Transmission", "VNA Reflection")
    shortModeNames = ("SA", "SATG", "VNATran", "VNARefl")

    def __init__(self, frame):
        self.frame = frame
        p = frame.prefs
        self.mode = p.get("mode", self.MODE_SA) # JGH: This is the default mode for EON
        # Exact frequency of the Master Clock (in MHz).
        self.masterclock = p.get("masterclock", 64.)
        # 2nd LO frequency (MHz). 1024 is nominal, Must be integer multiple
        # of PLL2phasefreq
        self.appxLO2 = p.get("appxLO2", 1024.)
        # list of Final Filter freq (MHz), bw (kHz) pairs
        ##self.RBWFilters = p.get("RBWFilters", [(10.698375, 8)])
        # JGH changed above line for next line
        self.RBWFilters = p.get("RBWFilters", [(10.7, 300.), (10.7, 30.), (10.7, 3.), (10.7, 0.3)]) # Defaults
        # selected Final Filter index
        self.indexRBWSel = self.switchRBW = i = p.get("indexRBWSel", 0)
        # Final Filter frequency, MHz; Final Filter bandwidth, KHz
        self.finalfreq, self.finalbw = self.RBWFilters[i]
        self.bitsRBW = 4 * i  # JGH 10/31/13
        # Video Filters
        self.vFilterNames = ["Wide", "Medium", "Narrow", "XNarrow"]
        self.vFilterCaps = p.get("vFilterCaps", [0.001, 0.1, 1.0, 10.0]) # Defaults
        self.vFilterSelIndex = p.get("vFilterSelIndex", 2) # JGH This is the default mode
        self.vFilterSelName = self.vFilterNames[self.vFilterSelIndex]
        self.bitsVideo = p.get("vFilterSelIndex", 2)
        self.cftest = p.get("cftest", 0)
        
        # SG output frequency (MHz)
        self._sgout = 10.
        # =0 if TG is normal, =1 if TG is in reverse.
        self._normrev = 0
        # TG offset frequency
        self._offset = 0
        # FWD/REV and TRANS/REFL
        self.switchFR = p.get("switchFR", 0)
        self.switchTR = p.get("switchTR", 0)
        self.bitsFR = 16 * self.switchFR
        self.bitsTR = 32 * self.switchTR
        # this assures Spur Test is OFF.
        self.spurcheck = 0
        # 1, 2 or 3, indicating bands 1G, 2G and 3G
        p.switchBand = p.get("switchBand", 1)
        if p.switchBand == 0:
            self.bitsBand = 64 * 0 # Band 2
            self._GHzBand = 2
        else:
            self.bitsBand = 64 * 1 # Bands 1 and 3
            self._GHzBand = 1 # (or 3)
        # Pulse switch
            self.switchPulse = p.get("switchPulse", 0)
            self.bitsPulse = 128 * self.switchPulse
        # set when in inverted-phase mode
        self.invPhase = 0
        # amount (degrees) to subtract from phase to invert it
        self.invDeg = p.get("invDeg", 180.)    # Default on Startup
        # set when running calibration
        self.calibrating = False
        # calibration level (0=None, 1=Base, 2=Band)
        self.calLevel = p.get("calLevel", 0)
        p.calLevel = self.calLevel = 0 # EON Jan 13 2013
        # calibration arrays, if present
        self.baseCal = None # Calibration of through response with a genereric wideband sweep
        self.bandCal = None #
        # set when calibration data doesn't align with current spectrum
        self.calNeedsInterp = False
        self.oslCal = None # EON Jan 10 2014
        # set when doing a scan
        self._scanning = False
        # results from last CaptureOneStep()
        self._magdata = 0
        self._phasedata = 0
        self._Sdb = 0
        self._Sdeg = 0
        self.fixtureR0 = float(p.get("fixtureR0", "50"))

        # magnitude correction table ADC values
        self.magTableADC = []
        # magnitude correction table true magnitudes
        self.magTableDBm = []
        # magnitude correction table phase adjustments
        self.magTablePhase = []
        # frequency-dependent magnitude correction table frequencies
        self.freqTableMHz = []
        # frequency-dependent magnitude correction table magnitude adjustements
        self.freqTableDBM = []

        # requested frequencies to scan
        self._fStart = None
        self._fStop = None
        self._nSteps = 0
        self._freqs = []
        self._step = 0
        # step history for maintaining continuity
        self._Hquad = 0
        self._history = []
        self._baseSdb = 0
        self._baseSdeg = 0
        # debugging events list
        self._events = []
        # error message queue, sent to GUI to display
        self.errors = Queue()
        # queue of scan results per step: Sdb, Sdeg, etc.
        self.scanResults = Queue()
        # active Synthetic DUT
        self.syndut = None  # JGH 2/8/14 syndutHook1

    #--------------------------------------------------------------------------
    # Log one MSA event, given descriptive string. Records current time too.

    def LogEvent(self, what):
        if logEvents: # EON Jan 22, 2014
            self._events.append(Event(what))

    #--------------------------------------------------------------------------
    # Dump list of events to log.

    def DumpEvents(self):
        print ("------------ MSA Events ---------------")
        for event in self._events:
            print ("%6d.%03d:" % (event.when/1000, event.when % 1000),event.what)
        print ("---------------------------------------")

    #--------------------------------------------------------------------------
    # Write debugging event lists to a file.

    def WriteEvents(self):
        events = [(e.when, "M  ", e.what) for e in self._events] + \
                 [(e.when, "GUI", e.what) for e in guiEvents]

        f = open("events.txt", "w")
        events.sort()
        t0 = events[0][0]
        for e in events:
            when = e[0] - t0
            f.write("%6d.%03d: %s %s\n" % (when/1000, when % 1000, e[1], e[2]))
        f.close()

    #--------------------------------------------------------------------------
    # Set major operating mode.

    def SetMode(self, mode):
        self.mode = mode

    #--------------------------------------------------------------------------
    # Return equivalent 1G frequency for f, based on _GHzBand.

    def _Equiv1GFreq(self, f):
        if self._GHzBand == 1:
            return f
        elif  self._GHzBand == 2:
            return f - LO2.freq
        else:
            return f - 2*(LO2.freq - self.finalfreq)

    #--------------------------------------------------------------------------
    # Calculate all steps for LO1 synth.

    def _CalculateAllStepsForLO1Synth(self, thisfreq, band):
        self._GHzBand = band
        if self._GHzBand != 1:
            # get equivalent 1G frequency
            thisfreq = self._Equiv1GFreq(thisfreq)
        # calculate actual LO1 frequency
        LO1.Calculate(thisfreq + LO2.freq - self.finalfreq)

    #--------------------------------------------------------------------------
    # Calculate all steps for LO3 synth.

    def _CalculateAllStepsForLO3Synth(self, TrueFreq, band):
        self._GHzBand = band
        if self._GHzBand == 1:
            thisfreq = TrueFreq
        else:
            # get equivalent 1G frequency
            thisfreq = self._Equiv1GFreq(TrueFreq)

        LO2freq = LO2.freq
        offset = self._offset
        if self.mode != self.MODE_SA:
            if self._normrev == 0:
                if self._GHzBand == 3:
                    # Mode 3G sets LO3 differently
                    LO3freq = TrueFreq + offset - LO2freq
                else:
                    # Trk Gen mode, normal
                    LO3freq = LO2freq + thisfreq + offset
            else:
                # Frequencies have been pre-calculated --
                # We can just retrieve them in reverse order.
                TrueFreq = self._freqs[self._nSteps - self._step]
                if self._GHzBand == 1:
                    revfreq = TrueFreq
                else:
                    # get equiv 1G freq
                    revfreq = self._Equiv1GFreq(TrueFreq)
                if self._GHzBand == 3:
                    # Mode 3G sets LO3 differently
                    LO3freq = TrueFreq + offset - LO2freq
                else:
                    # Trk Gen mode, normal
                    LO3freq = LO2freq + revfreq + offset

        else:
            # Sig Gen mode
            LO3freq = LO2freq + self._sgout

        LO3.Calculate(LO3freq)

    #--------------------------------------------------------------------------
    # _CommandAllSlims -- for SLIM Control and SLIM modules.
    # (send data and clocks without changing Filter Bank)
    #  0-15 is DDS1bit*4 + DDS3bit*16, data = 0 to PLL 1 and PLL 3.
    # (see CreateCmdAllArray). new Data with no clock,latch high,latch low,
    # present new data with clock,latch high,latch low. repeat for each bit.
    # (40 data bits and 40 clocks for each module, even if they don't need that many)
    # This format guarantees that the common clock will
    # not transition with a data transition, preventing crosstalk in LPT cable.

    # The attenuator code is left here for future implementation

##    def _CommandAllSlims(self, f):
    def _CommandAllSlims(self):    
        p = self.frame.prefs
        f = self._freqs[0]
        band = min(max(int(f/1000) + 1, 1), 3) # JGH Initial band

        if p.get("stepAtten", False) == True:
            # JGH: Attenuator will be using port P100 (a non existing port, TBD later) 
            if band != self.lastBand or p.stepAttenDB != self.lastStepAttenDB:
                # shift attenuator value into pair of 6-bit attenuators
                self._SetFreqBand(band)
                # each attenuator value is 0-31 in 0.5-dB increments
                value = int(p.stepAttenDB * 2)
                if 1:
                    # dual attenuators
                    if value > 0x3f:
                        value = (0x3f << 6) | (value - 0x3f)   # (bitwise OR)
                    for i in range(12):
                        bit = ((value >> 11) & 1) ^ 1
                        value <<= 1
                        self._SetFreqBand(band, (bit << cb.P100_AttenDataBit))
                        self._SetFreqBand(band, (bit << cb.P100_AttenDataBit) | cb.P100_AttenClk)
                        self._SetFreqBand(band, (bit << cb.P100_AttenDataBit))
                else:
                    if 0:
                        # clock scope loop
                        while 1:
                            self._SetFreqBand(band, 0)
                            self._SetFreqBand(band, cb.P100_AttenClk)

                    # single attenuator
                    for i in range(6):
                        bit = ((value >> 5) & 1) ^ 1
                        value <<= 1
                        self._SetFreqBand(band, (bit << cb.P100_AttenDataBit))
                        self._SetFreqBand(band, (bit << cb.P100_AttenDataBit) | cb.P100_AttenClk)
                        self._SetFreqBand(band, (bit << cb.P100_AttenDataBit))
                # latch attenuator value and give relays time to settle
                self._SetFreqBand(band, cb.P100_AttenLE)
                self._SetFreqBand(band)
                self.lastStepAttenDB = p.stepAttenDB
                cb.msWait(100)


        step1k = self.step1k ; step2k =self.step2k
        if p.sweepDir == 0:
            if ((step1k != None and self._step == (step1k - 1)) or \
                (step2k != None and self._step == (step2k - 1))):
                self.sendByteList()
                band = band + 1
                self._SetFreqBand(band)
                cb.msWait(100)
            else:
                self.sendByteList()
        if p.sweepDir == 1:
            if (self._step == (step1k) or self._step == (step2k)):
                self.sendByteList()
                band = band - 1
                self._SetFreqBand(band)
                cb.msWait(100)
            else:
                self.sendByteList()
    #--------------------------------------------------------------------------

    def sendByteList(self):            
        byteList = self.SweepArray[self._step]
        cb.SendDevBytes(byteList, cb.P1_Clk)    # JGH 2/9/14

        # print (">>>>> 2106, Remove data, leaving bitsRBW data to filter bank"
        #cb.SetP(1, self.bitsRBW) # JGH not needed here, instead use next line
        cb.SetP(1, 0)

        # send LEs to PLL1, PLL3, FQUDs to DDS1, DDS3, and command PDM
        # begin by setting up init word=LEs and Fquds + PDM state for thisstep
        pdmcmd = self.invPhase << cb.P2_pdminvbit
        # present data to buffer input
        cb.SetP(2, cb.P2_le1 + cb.P2_fqud1 + cb.P2_le3 + cb.P2_fqud3 + pdmcmd)
        # remove the added latch signal to PDM, leaving just the static data
        cb.SetP(2, pdmcmd)
        cb.setIdle
##        f = self._freqs[self._step]
##        band = min(max(int(f/1000) + 1, 1), 3) # JGH Values 1,2,3
##        if band != self.lastBand:
##            self.lastBand = band
##            # give PLLs more time to settle too
##        cb.msWait(100)
                     
    #--------------------------------------------------------------------------
    # Command just the PDM's static data.

    def _CommandPhaseOnly(self):
        cb.SetP(2, self.invPhase << cb.P2_pdminvbit)
        cb.setIdle()

    #--------------------------------------------------------------------------
    # Set the GHz frequency band: 1, 2, or 3.

    def _SetFreqBand(self, band, extraBits=0):
        self._GHzBand = band
        band += extraBits
        if self._GHzBand == 2:
            self.bitsBand = 64 * 0
        else:
            self.bitsBand = 64 * 1
        cb.SetP(4, self.bitsVideo + self.bitsRBW + self.bitsFR + \
                self.bitsTR + self.bitsBand + self.bitsPulse)
        ##print ("SetFreqBand: %02x" % band
        cb.setIdle()
        if debug:
            print ("G%02x" % band )

    #--------------------------------------------------------------------------
    # Initialize MSA hardware.

    def InitializeHardware(self):
        global cb, hardwarePresent, LO1, LO2, LO3

        if not hardwarePresent:
            return

        # Determine which interface to use to talk to the MSA's Control Board

        if not cb:
            if isWin and winUsesParallelPort:
                cb = MSA_CB_PC()
            else:
                cb = MSA_CB_USB()
                cb.FindInterface()
                if not cb.usbFX2 or not cb.ValidVersion():
                    cb = MSA_CB()
                    hardwarePresent = False
        else:
            # test interface to see that it's still there
            try:
                cb.OutPort(0)
                cb.Flush()
            except:
                cb = MSA_CB()
                hardwarePresent = False

        msaGlobal.SetCb(cb)

        if not hardwarePresent:
            print ("\n>>>2462<<<    NO HARDWARE PRESENT")
            print ("\n>>>2463<<< GENERATING SYNTHETIC DATA") # JGH syndutHook2
            from synDUT import SynDUTDialog
            self.syndut = SynDUTDialog(self.gui)
            wx.Yield()
            self.gui.Raise()

        p = self.frame.prefs

        # Instantiate MSA's 3 local oscillators
        PLL1phasefreq = p.get("PLL1phasefreq", 0.974)
        PLL2phasefreq = p.get("PLL2phasefreq", 4.000)
        PLL3phasefreq = p.get("PLL3phasefreq", 0.974)
        PLL1phasepol = p.get("PLL1phasepol", 0)
        PLL2phasepol = p.get("PLL2phasepol", 1)
        PLL3phasepol = p.get("PLL3phasepol", 0)
        appxdds1 =  p.get("appxdds1", 10.7)
        appxLO2 = p.get("appxLO2", 1024.)
        appxdds3 =  p.get("appxdds3", 10.7)
        dds1filbw = p.get("dds1filbw", 0.015)
        dds3filbw = p.get("dds3filbw", 0.015)
        PLL1type = p.get("PLL1type", "2326")
        PLL2type = p.get("PLL2type", "2326")
        PLL3type = p.get("PLL3type", "2326")
        # LO1 = MSA_LO(1, 0.,    cb.P1_PLL1DataBit, cb.P2_le1, cb.P2_fqud1, 0.974, 0, appxdds1, dds1filbw)
        # LO2 = MSA_LO(2, 1024., cb.P1_PLL2DataBit, cb.P2_le2, 0, 4.,    1, 0,        0)
        # LO3 = MSA_LO(3, 0.,    cb.P1_PLL3DataBit, cb.P2_le3, cb.P2_fqud3, 0.974, 0, appxdds3, dds3filbw)

        # JGH above three lines changed to
        LO1 = MSA_LO(1, 0., cb.P1_PLL1DataBit, cb.P2_le1, cb.P2_fqud1, \
                     PLL1phasefreq, PLL1phasepol, appxdds1, dds1filbw, PLL1type)
        LO2 = MSA_LO(2, appxLO2, cb.P1_PLL2DataBit, cb.P2_le2, 0, PLL2phasefreq, \
                     PLL2phasepol, 0, 0, PLL2type)
        LO3 = MSA_LO(3, 0., cb.P1_PLL3DataBit, cb.P2_le3, cb.P2_fqud3, PLL3phasefreq, \
                     PLL3phasepol, appxdds3, dds3filbw, PLL3type)
        # JGH change end

        # 5. Command Filter Bank to Path one. Begin with all data lines low
        cb.OutPort(0)
        # latch "0" into all SLIM Control Board Buffers
        cb.OutControl(cb.SELTINITSTRBAUTO)
        # begin with all control lines low
        cb.OutControl(cb.contclear)

        self._SetFreqBand(1)
##        self.lastBand = 1
        self.lastStepAttenDB = -1 # TODO: ATTENUATOR

        # 6.if configured, initialize DDS3 by reseting to serial mode.
        # Frequency is commanded to zero
        LO3.ResetDDSserSLIM()

        # 7.if configured, initialize PLO3. No frequency command yet.
        # JGH starts 2/1/14
        LO3.rcounter, LO3.pdf = LO3.CreateRcounter(LO3.appxdds)
        if debug:
            print(">>>2533<<< Step7 LO3.rcounter, LO3.pdf: ", LO3.rcounter, LO3.pdf)
        # JGH ends 2/1/14
        LO3.CommandPLLR()

        # 8.initialize and command PLO2 to proper frequency

        # CreatePLL2R (needs: appxpdf, masterclock)
        LO2.rcounter, LO2.pdf = LO2.CreateRcounter(msa.masterclock) # JGH starts 2/1/14
        #                       (creates: rcounter, pdf)
        if debug:
            print(">>>2543<<< Step8 LO2.rcounter, LO2.pdf: ", LO2.rcounter, LO2.pdf)

        # Command PLL2R and Init Buffers (needs:PLL2phasepolarity,SELT,PLL2)
        LO2.CommandPLLR()
        # CreatePLL2N
        appxVCO = self.appxLO2 # JGH: appxLO2 is the Hardware Config Dialog value
        # 8a. CreateIntegerNcounter(needs: PLL2 (aka appxVCO), rcounter, fcounter)
        LO2.CreateIntegerNcounter(appxVCO, msa.masterclock)
        #                     (creates: ncounter, fcounter(0))
        LO2.CreatePLLN()    # (needs: ncounter, fcounter, PLL2)
        #                     (creates: Bcounter,Acounter, and N Bits N0-N23)
        # 8b. Actual LO2 frequency
        LO2.freq = ((LO2.Bcounter*LO2.preselector) + LO2.Acounter + \
                    (LO2.fcounter/16))*LO2.pdf
        if debug:
            print(">>>2559<<< Step8b LO2.freq: ", LO2.freq)
        # 8c. CommandPLL2N
        # needs:N23-N0,control,Jcontrol=SELT,port,contclear,LEPLL=8
        # commands N23-N0,old ControlBoard
        LO2.CommandPLL(LO2.PLLbits)
        if debug:
            print(">>>2566<<< Step8c LO2 commanded ******")
        # 9.Initialize PLO 1. No frequency command yet.
        # CommandPLL1R and Init Buffers
        # needs:rcounter1,PLL1phasepolarity,SELT,PLL1
        # Initializes and commands PLL1 R Buffer(s)
        LO1.CommandPLLR()
        # 10.initialize DDS1 by resetting. Frequency is commanded to zero
        # It should power up in parallel mode, but could power up in a bogus
        #  condition. reset serial DDS1 without disturbing Filter Bank or PDM
        LO1.ResetDDSserSLIM()   # SCOTTY TO MODIFY THIS TO LIMIT HIGH CURRENT

        # JGH added 10a. Set Port 4 switches 2/24/14
        self.vFilterSelIndex = p.get("vFilterSelIndex", 1)   # Values 0-3
        self.switchRBW = p.get("switchRBW", 0) # Values 0-3
        self.switchFR = p.get("switchFR", 0) # Values 0,1
        self.switchTR = p.get("switchTR", 0) # Values 0,1
        self.switchBand = p.get("switchBand", 1) # 1: 0-1GHz, 2: 1-2GHz, 3: 2-3GHz
        self.switchPulse = 0 # JGH Oct23 Set this here and follow with a 1 sec delay
        # Pulse must be added here, in the mean time use 0
        self.bitsVideo = self.vFilterSelIndex # V0/V1 Bits 0,1
        self.bitsRBW = 4 * self.switchRBW # A0/A1 Bits 2, 3
        self.bitsFR = 16 * self.switchFR # Bit 4
        self.bitsTR = 32 * self.switchTR    # Bit 5
        self.bitsBand = 64 * self. switchBand # G0 Bit 6
        self.bitsPulse = 128 * self.switchPulse # Bit 7
        
        cb.SetP(4, self.bitsVideo + self.bitsRBW + self.bitsFR + \
                self.bitsTR + self.bitsBand + self.bitsPulse)
        if debug:
            print(">>>2580<<< Steps9/10 commanded and switches set ************")
        # JGH addition ended
    #--------------------------------------------------------------------------
    # Read 16-bit magnitude and phase ADCs.

    def _ReadAD16Status(self):
        # Read16wSlimCB --
        mag, phase = cb.GetADCs(16)
        mag   >>= cb.P5_MagDataBit
        phase >>= cb.P5_PhaseDataBit
        self._magdata = mag
        self._phasedata = 0x10000 - phase
        if debug:
            print ("_ReadAD16Status: mag=0x%x, phase=0x%x" % \
                    (self._magdata, self._phasedata))

    #--------------------------------------------------------------------------
    # Use synthetic data as input.

    def _InputSynth(self, f): # JGH 2/8/14 syndutHook3
        syndut = self.syndut
        nf = len(syndut.synSpecF)
        nM = len(syndut.synSpecM)
        nP = len(syndut.synSpecP)
        if nf != nM or nf != nP:
            print ("msa.InputSynth: length mismatch: nf=%d nM=%d nP=%d" % \
                (nf, nM, nP))
        else:
            self._magdata =   interp(f, syndut.synSpecF, syndut.synSpecM)
            self._phasedata = interp(f, syndut.synSpecF, syndut.synSpecP)

    #--------------------------------------------------------------------------
    # Capture magnitude and phase data for one step.

    def CaptureOneStep(self, post=True, useCal=True, bypassPDM=False):
        p = self.frame.prefs  # JGH/SCOTTY 2/6/14
        step = self._step
        if logEvents:
            self._events.append(Event("CaptureOneStep %d" % step))
        f = self._freqs[step]
##        print (">>>2572<<< step: ", step , ", f: ", f)
        if f < -48:
            Sdb = nan
            Sdeg = nan
            Mdb = nan
            Mdeg = nan
        else:
            doPhase = self.mode > self.MODE_SATG
            #invPhase = self.invPhase
            if hardwarePresent:
                self.LogEvent("CaptureOneStep hardware, f=%g" % f)
                # set MSA to read frequency f
##                self._CommandAllSlims(f) # SweepArray doesn't need f
                self._CommandAllSlims()

    # ------------------------------------------------------------------------------
                if p.cftest ==1:
##                      cavityLO2 = msa.finalfreq + LO1.freq
                    cavityLO2 =1013.3 + msa.finalfreq + f
                    print ("freq: ", f)
                    LO2.CreateIntegerNcounter(cavityLO2, msa.masterclock)
                    LO2.CreatePLLN()
                    LO2.freq = ((LO2.Bcounter*LO2.preselector) + LO2.Acounter+(LO2.fcounter/16))*LO2.pdf
                    LO2.CommandPLL(LO2.PLLbits)
    # ------------------------------------------------------------------------------
                self.LogEvent("CaptureOneStep delay")
                if step == 0:
                    # give the first step extra time to settle
                    cb.msWait(200)
##                    self._CommandAllSlims(f)
                    self._CommandAllSlims() # SweepArray doesn't need f
                cb.msWait(self.wait)
                # read raw magnitude and phase
                self.LogEvent("CaptureOneStep read")
                cb.ReqReadADCs(16)
                cb.FlushRead()
                cb.Flush()
                time.sleep(0)
                self._ReadAD16Status()
                if logEvents: # EON Jan 22, 2014
                    self._events.append(Event("CaptureOneStep got %06d" % \
                                    self._magdata))
                if self._magdata < goodPhaseMagThreshold:
                    doPhase = False

                # check if phase is within bad quadrant, invert phase and
                # reread it
                # JGH This shall be modified if autoPDM is used (Nov 9, 2013)
                if doPhase and not bypassPDM and \
                        (self._phasedata < 13107 or self._phasedata > 52429):
                    oldPhase = self._phasedata
                    self.invPhase = 1 - self.invPhase
                    self._CommandPhaseOnly()
                    self.LogEvent("CaptureOneStep phase delay")
                    cb.msWait(200)
                    self.LogEvent("CaptureOneStep phase reread")
                    cb.ReqReadADCs(16)
                    cb.FlushRead()
                    cb.Flush()
                    time.sleep(0)
                    self._ReadAD16Status()
                    # inverting the phase usually fails when signal is noise
                    if self._phasedata < 13107 or self._phasedata > 52429:
                        print ("invPhase failed at %13.6f mag %5d orig %5d new %5d" %
                               (f, self._magdata, oldPhase, self._phasedata))
                        self.invPhase = 1 - self.invPhase

            else:
                self.LogEvent("CaptureOneStep synth, f=%g" % f)
##                self._InputSynth(f) # JGH syndutHook4
                #invPhase = 0
                cb.msWait(self.wait)
                # sleep for 1 ms to give GUI a chance to catch up on key events
                time.sleep(0.001)

            ##print ("Capture: magdata=", self._magdata
            if useCal and len(self.magTableADC) > 0:
                # adjust linearity of values using magTable
                Sdb = interp(self._magdata, self.magTableADC, self.magTableDBm)
                ##print ("Capture: Sdb=", Sdb
                ##self.LogEvent("CaptureOneStep magTableADC")
            else:
                # or just assume linear and estimate gain
                Sdb = (self._magdata / 65536 - 0.5) * 200
                ##self.LogEvent("CaptureOneStep Linear estimate")

            if useCal and len(self.freqTableMHz) > 0:
                # correct the magnitude based on frequency
                ##print ("Capture: Sdb=", Sdb, "f=", f, self.freqTableMHz[-1]
                if f <= self.freqTableMHz[-1]:
                    Sdb += interp(f, self.freqTableMHz, self.freqTableDB)
                else:
                    Sdb += self.freqTableDB[-1]
                ##print ("Capture: Sdb=", Sdb, "after freqTableMHz"
            Mdb = Sdb

            if doPhase:
                if bypassPDM:
                    Sdeg = modDegree(self._phasedata / 65536 * 360)
                else:
                    Sdeg = modDegree(self._phasedata / 65536 * 360 - \
                            self.invPhase * self.invDeg)
                # phase in 3G band is inverted
                if self._GHzBand == 3:
                    Sdeg = -Sdeg
                ##print ("%4dM: %5d %6.2f  %5d %6.2f" % (f, self._magdata,
                ##       Sdb, self._phasedata, Sdeg)

                if useCal:
                    # look up phase correction in magTable
                    if len(self.magTableADC) > 0:
                        diffPhase = interp(self._magdata, self.magTableADC,
                                            self.magTablePhase)
                        # a diffPhase near 180 deg indicates the phase is
                        # invalid: set it to 0
                        if abs(diffPhase) >= 179:
                            Sdeg = 0.
                        else:
                            Sdeg = modDegree(Sdeg - diffPhase)

                    # add in plane extension in ns. (0.001 = ns*MHz)
                    planeExt = self._planeExt[self._GHzBand-1]
                    Sdeg = modDegree(Sdeg + 360 * f * planeExt * 0.001)
            else:
                Sdeg = nan
            Mdeg = Sdeg

            # determine which phase cycle this step is in by comparing
            # its phase quadrant to the previous one and adjusting the
            # base when it wraps
            if isnan(Sdeg):
                Squad = 0
            else:
                Squad = int((Sdeg + 180) / 90)
            if self._Hquad == 3 and Squad == 0:
                self._baseSdeg += 360
            elif self._Hquad == 0 and Squad == 3:
                self._baseSdeg -= 360
            self._Hquad = Squad

            # always make a continuous phase (wrapped phase is taken from it)
            Sdeg += self._baseSdeg

            # if enabled, make mag continuous so cal interp doesn't glitch
            if self._contin:
                Sdb += self._baseSdb

                hist = self._history
                show = False
                if len(hist) > 1:
                    # H2f is frequency at current step minus 2, etc.
                    H2f, H2db, H2deg = hist[0]
                    H1f, H1db, H1deg = hist[1]

                    if f > 500 and (f // 1000) != (H1f // 1000):
                        # just crossed into a new GHz band: adjust bases
                        dSdb  = Sdb  - (2*H1db  - H2db )
                        dSdeg = Sdeg - (2*H1deg - H2deg)
                        self._baseSdb  -= dSdb
                        self._baseSdeg -= dSdeg
                        Sdb  -= dSdb
                        Sdeg -= dSdeg
                        if show:
                            print ("jumped gap=", f, H1f, f // 1000, \
                                H1f // 1000, dSdb)
                    if show:
                        print ("hist=", ["%7.2f %7.2f %7.2f" % x for x in hist], \
                            "Sdb=%7.2f" % Sdb, "Sdeg=%7.2f" % Sdeg, \
                            "Hq=%d" % self._Hquad, "Sq=%1d" % Squad, \
                            "bases=%7.2f" % self._baseSdb, \
                            "%7.2f" % self._baseSdeg)
                    hist.pop(0)

                # keep history
                hist.append((f, Sdb, Sdeg))


            # subtract any selected base or band calibration
            if not self.calibrating:
                cal = (None, self.baseCal, self.bandCal)[self.calLevel]
                if cal:
                    # Start EON Jan 10 2014
                    if msa.mode == MSA.MODE_VNARefl:
                        if cal.oslCal:
                            calM, calP = cal[step]
                            Sdb -= calM
                            Sdeg -= calP
                            (Sdb, Sdeg) = cal.ConvertRawDataToReflection(step, Sdb, Sdeg)
                    else:
                    # End EON Jan 10 2014
                        if self.calNeedsInterp:
                            calM = interp(f, cal.Fmhz, cal.Sdb)
                            calP = interp(f, cal.Fmhz, cal.Scdeg)
                        else:
                            calF, calM, calP = cal[step]
                        Sdb -= calM
                        if doPhase:
                            Sdeg -= calP

        # either pass the captured data to the GUI through the scanResults
        # buffer if 'post' set, or return it
        self._Sdb = Sdb
        Scdeg = Sdeg
        self._Sdeg = Sdeg = modDegree(Sdeg)
        self.LogEvent("CaptureOneStep done, Sdb=%g Sdeg=%g" % (Sdb, Sdeg))
        if post:
            self.scanResults.put((step, Sdb, Sdeg, Scdeg,
                self._magdata, self._phasedata, Mdb, Mdeg, msElapsed()))
        else:
            return f, self._magdata, Sdb, Sdeg

    #--------------------------------------------------------------------------
    # Internal scan loop thread.

    def _ScanThread(self):
        try:
            self.LogEvent("_ScanThread")

            # clear out any prior FIFOed data from interface
            cb.Clear()
            elapsed = 0
            while self.scanEnabled:
                if logEvents: # EON Jan 22, 2014
                    self._events.append(Event("_ScanThread wloop, step %d" % \
                                              self._step))
                self.CaptureOneStep()
                self.NextStep()
                elapsed += int(self.wait) + 3
                self.LogEvent("_ScanThread: step=%d Req.nSteps=%d" % \
                              (self._step, self._nSteps))
                if self._step == 0 or self._step == self._nSteps+1:
                    if self.haltAtEnd:
                        self.LogEvent("_ScanThread loop done")
                        self.scanEnabled = False
                        break
                    else:
                        self.LogEvent("_ScanThread to step 0")
                        self.WrapStep()
                # yield some time to display thread
                if elapsed > msPerUpdate:
                    elapsed = 0
                    evt = UpdateGraphEvent()
                    wx.PostEvent(self.gui, evt)

        except:
            self.showError = True
            traceback.print_exc()
            self.showError = False

        if self.haltAtEnd:
            self.scanEnabled = False

        self.LogEvent("_ScanThread exit")
        self._scanning = False

    #--------------------------------------------------------------------------
    # Stop any current scan and set up for a new spectrum scan.

    def NewScanSettings(self, parms):

        self.LogEvent("NewScanSettings nSteps=%d" % parms.nSteps)

        # if scan already running, disable it and wait for it to finish
        if self._scanning:
            self.scanEnabled = False
            while self._scanning:
                time.sleep(0.1)

        # set current parameters to given values
        self.wait        = parms.wait
        self._sgout      = parms.sigGenFreq
        self._offset     = parms.tgOffset
        self.invDeg      = parms.invDeg
        self._planeExt   = parms.planeExt
        self._normrev    = parms.normRev
        self._sweepDir   = parms.sweepDir
        self._isLogF     = parms.isLogF
        self._contin     = parms.continuous

        # set start and stop frequencies, swapped if sweeping downward
        fStart = parms.fStart
        fStop  = parms.fStop

        if self._sweepDir == 1:
            self._sweepInc = -1
            self._fStart = fStop
            self._fStop  = fStart
        else:
            self._sweepInc = 1
            self._fStart = fStart
            self._fStop  = fStop
            
        self._nSteps = nSteps = parms.nSteps


        # create array of frequencies in scan range, linear or log scale
        if self._isLogF:
            parms.fStart = fStart = max(fStart, 1e-6)
            self._freqs = logspace(log10(fStart), log10(fStop), num=nSteps+1)
        else:
            # JGH linspace comes from numpy
            # self._freqs = linspace(self._fStart, self._fStop, nSteps+1)
            self._freqs = linspace(fStart, fStop, nSteps+1)

        self.step1k = self.step2k = None            
        for x, y in enumerate(self._freqs):
            if y == 1000:
                self.step1k = x ; print("1000 is at step #", x)
            if y == 2000:
                self.step2k = x ; print("2000 is at step #", x)

    #--------------------------------------------------------------------------
    # Start an asynchronous scan of a spectrum. The results may be read at
    # any time. Returns True if scan wasn't already running.

    def ConfigForScan(self, gui, parms, haltAtEnd):
        self.LogEvent("Scan")
        if self._scanning:
            return False
        self.gui = gui
        self.haltAtEnd = haltAtEnd
        self.NewScanSettings(parms)
        self.InitializeHardware()
        self._step = 0
        self._history = []
        self._baseSdb = 0
        self._baseSdeg = 0
        self.ContinueScan()
        self.LogEvent("Scan exit")
        return True

    #--------------------------------------------------------------------------
    # Continue a halted scan starting at the current step.

    def ContinueScan(self):
        self.LogEvent("ContinueScan: step=%d" % self._step)
        if not self._scanning:
            self.CreateSweepArray() # Creates GEORGE
            self.LogEvent("ContinueScan start_new_thread")
            self.scanEnabled = self._scanning = True
            thread.start_new_thread(self._ScanThread, ())
        self.LogEvent("ContinueScan exit")

    #--------------------------------------------------------------------------
    # SweepArray
    # (send data and clocks without changing Filter Bank)
    #  0-15 is DDS1bit*4 + DDS3bit*16, data = 0 to PLL 1 and PLL 3.
    # (see CreateCmdAllArray). new Data with no clock,latch high,latch low,
    # present new data with clock,latch high,latch low. repeat for each bit.
    # (40 data bits and 40 clocks for each module, even if they don't need that many)
    # This format guarantees that the common clock will
    # not transition with a data transition, preventing crosstalk in LPT cable.

    def CreateSweepArray(self): # aka GEORGE
        if 1:
            print(">>>2975<<< Creating GEORGE, the SweepArray")
        
        SweepArray = []
       
        #p = self.frame.prefs
        for f in self._freqs:
            band = min(max(int(f/1000) + 1, 1), 3) # JGH Values 1,2,3
            
            self._CalculateAllStepsForLO1Synth(f, band)
            self._CalculateAllStepsForLO3Synth(f, band)
            
            # PLLs go out MSB first, with a 16-bit leader of zeros
            PLL1bits = LO1.PLLbits
            PLL3bits = LO3.PLLbits
            msb = 23 + 16
            shift1 = msb - cb.P1_PLL1DataBit
            shift3 = msb - cb.P1_PLL3DataBit
            mask = 1 << msb
            # pre-shift 40 bits for each DDS so the LSB aligns with its port
            # serial-data bit
            DDS1bits = LO1.DDSbits << cb.P1_DDS1DataBit
            DDS3bits = LO3.DDSbits << cb.P1_DDS3DataBit
            if debug:
                print ("PLL1bits=0x%010x" % PLL1bits)
                print ("DDS1bits=0x%010x" % DDS1bits)
                print ("DDS3bits=0x%010x" % DDS3bits)

            byteList = []   # JGH 2/9/14
            for i in range(40):
                # combine the current bit for each device and clk them out together
    ##            a = (DDS3bits & cb.P1_DDS3Data) + ((PLL3bits & mask) >> shift3) + \
    ##                (DDS1bits & cb.P1_DDS1Data) + ((PLL1bits & mask) >> shift1) + \
    ##                self.bitsRBW # JGH line changed for next one
                a = (DDS3bits & cb.P1_DDS3Data) + ((PLL3bits & mask) >> shift3) + \
                    (DDS1bits & cb.P1_DDS1Data) + ((PLL1bits & mask) >> shift1)
                byteList.append(a)  # JGH 2/9/14
                # shift next bit into position
                DDS3bits >>= 1; PLL3bits <<= 1; DDS1bits >>= 1; PLL1bits <<= 1
            SweepArray.append(byteList)
        if 1:
            print(">>>3015<<< arraySweeper finished with length: ", len(SweepArray))

        #step1k = self.step1k ; step2k =self.step2k
        self.SweepArray = SweepArray
            
    #--------------------------------------------------------------------------
    # Stop current scan.

    def StopScan(self):
        self.LogEvent("StopScan")
        self.scanEnabled = False

    #--------------------------------------------------------------------------
    # Return True if scan running.

    def IsScanning(self):
        return self._scanning

    #--------------------------------------------------------------------------
    # Get, wrap-around, or increment step number.

    def GetStep(self):
        return self._step

    def WrapStep(self):
        lastStep = self._step
        self._step = lastStep % (self._nSteps+1)
        if abs(self._step - lastStep) > 1:
            self._baseSdb = 0
            self._baseSdeg = 0
            self._history = []

    def NextStep(self): # EN 12/23/13 Modified this method as follows
        if self._sweepDir == 2:
            # sweep back and forth
            if (self._step == self._nSteps) and (self._sweepInc == 1): # EN 12/23/13
                self._sweepInc = -1
            elif (self._step == 0) and (self._sweepInc == -1):
                self._sweepInc = 1
            else:
                self._step += self._sweepInc
        elif self._sweepDir == 1:
            # sweep right to left
            if self._step == 0:
                self._step = self._nSteps
                self._sweepInc = -1
            else:
                self._step += self._sweepInc
        else:
            self._step += self._sweepInc

    #--------------------------------------------------------------------------
    # Return a string of variables and their values for the Variables window.

    def GetVarsTextList(self):
        step = max(self._step - 1, 0)
        return [
            "this step = %d" % step,
            "dds1output = %0.9g MHz" % LO1.ddsoutput,
            "LO1 = %0.9g MHz" % LO1.freq,
            "pdf1 = %0.9g MHz" % LO1.pdf,
            "ncounter1 = %d" % LO1.ncounter,
            "Bcounter1 = %d" % LO1.Bcounter,
            "Acounter1 = %d" % LO1.Acounter,
            "fcounter1 = %d" % LO1.fcounter,
            "rcounter1 = %d" % LO1.rcounter,
            "LO2 = %0.6f MHz" % LO2.freq,
            "pdf2 = %0.6f MHz" % LO2.pdf,
            "ncounter2 = %d" % LO2.ncounter,
            "Bcounter2 = %d" % LO2.Bcounter,
            "Acounter2 = %d" % LO2.Acounter,
            "rcounter2 = %d" % LO2.rcounter,
            "LO3 = %0.6f MHz" % LO3.freq,
            "pdf3 = %0.6f MHz" % LO3.pdf,
            "ncounter3 = %d" % LO3.ncounter,
            "Bcounter3 = %d" % LO3.Bcounter,
            "Acounter3 = %d" % LO3.Acounter,
            "fcounter3 = %d" % LO3.fcounter,
            "rcounter3 = %d" % LO3.rcounter,
            "dds3output = %0.9g MHz" % LO3.ddsoutput,
            "Magdata=%d mag=%0.5g" % (self._magdata, self._Sdb),
            "Phadata=%d PDM=%0.5g" % (self._phasedata, self._Sdeg),
            "Real Final I.F. = %f" % (LO2.freq  - 0),
            "Masterclock = %0.6f" % msa.masterclock

        ]

    #--------------------------------------------------------------------------
    # Spectrum accessors.

    def HaveSpectrum(self):
        return self._fStart != None

    def NewSpectrumFromRequest(self, title):
        return Spectrum(title, self.indexRBWSel+1, self._fStart, self._fStop,
                        self._nSteps, self._freqs)


#******************************************************************************
#****                          MSA GUI Front End                          *****
#******************************************************************************

# Waveform display colors

# light-theme colors
red       = wx.Colour(255,   0,   0)
blue      = wx.Colour(  0,   0, 255)
green     = wx.Colour(  0, 255,   0)
aqua      = wx.Colour(  0, 255, 255)
lavender  = wx.Colour(255,   0, 255)
yellow    = wx.Colour(255, 255,   0)
peach     = wx.Colour(255, 192, 203)
dkbrown   = wx.Colour(165,  42,  42)
teal      = wx.Colour(  0, 130, 130)
brown     = wx.Colour(130,   0, 130)
stone     = wx.Colour(240, 230, 140)
orange    = wx.Colour(255, 165,   0)
ltgray    = wx.Colour(180, 180, 180)

# original MSA dark-theme colors
msaGold   = wx.Colour(255, 190,  43)
msaAqua   = aqua
msaGreen  = wx.Colour(0,   255,   0)
msaYellow = wx.Colour(244, 255,   0)
msaGray   = wx.Colour(176, 177, 154)


#==============================================================================
# A Color theme.

class Theme:

    @classmethod
    def FromDict(cls, d):
        d = Struct(**d)
        this = cls()
        this.name       = d.name
        this.backColor  = d.backColor
        this.foreColor  = d.foreColor
        this.hColor     = d.hColor
        this.vColors    = d.vColors
        this.gridColor  = d.gridColor
        this.textWeight = d.textWeight
        this.iNextColor = 0
        return this

    def UpdateFromPrefs(self, p):
        for attrName in self.__dict__.keys():
            pAttrName = "theme_%s_%s" % (p.graphAppear, attrName)
            if hasattr(p, pAttrName):
                setattr(self, attrName, getattr(p, pAttrName))

    def SavePrefs(self, p):
        for attrName, attr in self.__dict__.items():
            if attrName[0] != "_":
                pAttrName = "theme_%s_%s" % (p.graphAppear, attrName)
                setattr(p, pAttrName, attr)

DarkTheme = Theme.FromDict(dict(
        name       = "Dark",
        backColor  = wx.BLACK,
        foreColor  = wx.WHITE,
        gridColor  = msaGray,
        hColor     = wx.WHITE,
        vColors    = [msaGold, msaAqua, msaGreen, msaYellow, teal, brown],
        textWeight = wx.BOLD))

LightTheme = Theme.FromDict(dict(
        name       = "Light",
        backColor  = wx.WHITE,
        foreColor  = wx.BLACK,
        gridColor  = ltgray,
        hColor     = wx.BLACK,
        vColors    = [red, blue, green, blue, aqua, lavender, yellow, peach,
                      dkbrown, stone, orange],
        textWeight = wx.NORMAL))


#==============================================================================
# Preferences as attributes.

class Prefs:
    def __init__(self):
        self._fName = None

    #--------------------------------------------------------------------------
    # Read a preferences file and translate it into attributes.

    @classmethod
    def FromFile(cls, fName):
        this = cls()
        this._fName = fName
        try:
            af = open(fName, "r")
            configPat = re.compile(r"^[ \t]*([^=]+)[ \t]*=[ \t]*(.*)$")

            for line in af.readlines():
                ##print ("line=", line)
                m = configPat.match(line)
                if m and line[0] != "|":
                    name, value = m.groups()
                    ##print ("parameter", name, "=", value)
                    try:
                        value = eval(value)
                    except:
                        pass
                    setattr(this, name, value)

            af.close()
        except IOError:
            print ("No prefs file found. Getting defaults.")
        return this

    #--------------------------------------------------------------------------
    # Save given preferences to file.

    def save(self, fName=None, header=None):
        if not fName:
            fName = self._fName
        pf = open(fName, "w")
        if header:
            pf.write("|%s\n" % header)

        for name in sorted(dir(self)):
            value = getattr(self, name)
            ##print ("Saving pref", name, value)
            if name[0] != '_' and type(value) != type(self.__init__):
                if type(value) == type(1.):
                    value = str(value)
                elif name == "theme":
                    value = value.name
                elif name.find(" "):
                    value = repr(value)
                pf.write("%s=%s\n" % (name, value))
        pf.close()

    #--------------------------------------------------------------------------
    # Get a preference value, possibly using the default.

    def get(self, name, defaultValue):
        if not hasattr(self, name):
            setattr(self, name, defaultValue)
        value = getattr(self, name)
        if type(value) != type(defaultValue) and type(value) == type(""):
            try:
                value = eval(value)
            except:
                pass
        return value


#==============================================================================
# The message pane and log file to which stdout and stderr are redirected to.

class Logger:
    def __init__(self, name, textCtrl, frame):
        global logFile
        logFile = file(name + ".log", "w+")
        self.textCtrl = textCtrl
        self.frame = frame
        self.lineCount = 0

    def write(self, s):
        global logFile
        try:
            if "\n" in s:
                self.lineCount += 1
            maxLogWinLines = 10000
            if wx.Thread_IsMain() and self.lineCount < maxLogWinLines:
                logFile.write(s)
                self.textCtrl.AppendText(s)
            elif self.lineCount < maxLogWinLines or msa.showError:
                msa.errors.put(s)
        except:
            pass # don't recursively crash on errors


#==============================================================================
# Calculate spacing for a standard 1-2-5 sequence scale.
#   bot:    bottom range of scale (units)
#   top:    top range of scale (units)
#   size:   size of space for scale (pixels)
#   divSize: target size of one division (pixels)
# returns:
#   ds:     units per division
#   base:   next division equal to or above bot (units)
#   frac:   remainer between base and bot (units)
#   nDiv:   number of divisions in scale

def StdScale(bot, top, size, divSize):
    wid = top - bot
    dsExp, dsMantNo = divmod(log10(wid * divSize / size), 1)
    if isnan(dsMantNo):
        print ("StdScale dsMantNo=", dsMantNo)
        return 1, 0, 0, 0
    ds = (1.0, 2.0, 5.0, 5.0)[int(3 * dsMantNo + 0.1)] * 10**dsExp
    base = floor(bot/ds + 0.95) * ds
    frac = base - bot
    nDiv = max(int(wid/ds), 0) + 1
    return ds, base, frac, nDiv


#==============================================================================
# A graph vertical axis scale.
# Each trace refers to one of these, and these in turn refer to one primary
# (or only) trace for their color. The scale is placed on either side of the
# graph, with higher-priority scales on the inside.

class VScale:
    def __init__(self, typeIndex, mode, top, bot, primeTraceUnits):
        self.typeIndex = typeIndex
        self.top = top
        self.bot = bot
        self.maxHold = False
        self.primeTraceUnits = primeTraceUnits
        typeList = traceTypesLists[mode]
        self.dataType = typeList[min(typeIndex, len(typeList)-1)]

    #--------------------------------------------------------------------------
    # Perform auto-scale on limits to fit data.

    def AutoScale(self, frame):
        dataType = self.dataType
        if dataType.units == "Deg":
            self.top = 180
            self.bot = -180
        elif self.typeIndex > 0:
            tr = dataType(frame.spectrum, 0)
            v = tr.v
            vmin = v[v.argmin()]
            vmax = v[v.argmax()]
            if isfinite(vmin) and isfinite(vmax) and vmax > vmin:
                print ("Auto scale: values from", vmin, "to", vmax)
                # round min/max to next even power
                ds, base, frac, nDiv = StdScale(vmin, vmax, 1., 1.)
                print ("Auto scale: ds=", ds, "base=", base, "frac=", frac, "nDiv=", nDiv)
                if isfinite(base) and isfinite(ds):
                    if frac == 0:
                        bot = base
                    else:
                        bot = base - ds
                    top = bot + ds*nDiv
                    if top < vmax:
                        top += ds
                else:
                    bot = tr.bot
                    top = tr.top
            else:
                bot = tr.bot
                top = tr.top
            self.top = top
            self.bot = bot

    #--------------------------------------------------------------------------
    # Open the Vertical Scale dialog box and apply to this scale.

    def Set(self, frame, pos):
        specP = frame.specP
        dlg = VScaleDialog(specP, self, pos)
        save = dcopy.copy(self)
        if dlg.ShowModal() == wx.ID_OK:
            dlg.Update()
        else:
            self.top = save.top
            self.bot = save.bot
            self.typeIndex = save.typeIndex
            self.dataType = save.dataType
            frame.DrawTraces()
            specP.FullRefresh()

#==============================================================================
# A frequency marker for the spectrum frame.

class Marker:
    # marker mode, set in menu
    MODE_INDEP = 1      # Independent
    MODE_PbyLR = 2      # P+,P- bounded by L,R
    MODE_LRbyPp = 3     # L,R bounded by P+
    MODE_LRbyPm = 4     # L,R bounded by P-

    def __init__(self, name, traceName, mhz):
        self.name = name            # "L", "1", etc.
        self.traceName = traceName  # name of trace it's on
        self.mhz = mhz              # frequency (MHz)
        self.dbm = 0                # mangitude (dBm)
        self.deg = 0                # phase (degrees)

    #--------------------------------------------------------------------------
    # Find position of peak in given spectrum.
    #
    # data:     spectrum in dBm.
    # df:       freq bin spacing, in MHz.
    # iLogF:    True for log-frequency mode.
    # isPos:    True for P+, False for P-.
    # f0:       frequency of data[0]

    def FindPeak(self, data, df, isLogF, isPos=True, f0=0):
        if isLogF:
            f0 = log10(max(f0, 1e-6))
        n = len(data)
        if n > 0:
            if isPos:
                peak = data.argmax()
            else:
                peak = data.argmin()
            mhz = f0 + peak * df
            if isLogF:
                mhz = 10**mhz
            ##print ("FindPeak: mhz=", mhz, data
            self.mhz = round(mhz, 6)

    #--------------------------------------------------------------------------
    # Find position of peak in given spectrum by fitting a polygon near the
    # peak. This method is better suited for a smooth peak with few samples.
    #
    # data:     spectrum in dBm.
    # df:       freq bin spacing, in MHz.
    # isPos:    True for P+, False for P-.
    # f0:       frequency of data[0]
    # pdev:     how many elements to include on either side of the peak.

    def FindPeakPoly(self, data, df, isPos=True, f0=0, pdev=5):
        n = len(data)
        Pi = (data.argmin(), data.argmax())[isPos]
        if Pi < pdev:
            pdev = Pi
        elif Pi+pdev > n:
            pdev = n-Pi
        if pdev == 0:
            # no width to peak: use center
            self.mhz = round(f0 + Pi * df, 6)
            self.dbm = data[Pi]
        else:
            # fit peak data segment to a polynomial
            Li = Pi-pdev
            Ri = Pi+pdev
            peakPart = data[Li:Ri]
            indecies = arange(2*pdev)
            warnings.simplefilter("ignore", RankWarning)
            if len(indecies) != len(peakPart):
                # no width to peak: use center
                self.mhz = round(f0 + Pi * df, 6)
                self.dbm = data[Pi]
            else:
                if peakPart[0] < -1e6:
                    return
                p = polyfit(indecies, peakPart, 4)
                pp = poly1d(p)
                # peak is where slope is zero
                dpRoots = pp.deriv().roots
                self.dpRoots = dpRoots
                # must be at least one real root for a degree 3 poly
                pos = 0
                minj = 999.
                maxr = 2*pdev - 1
                for root in dpRoots:
                    rj = abs(root.imag)
                    if rj < minj and root.real >= 0 and root.real <= maxr:
                        pos = root.real
                        minj = rj
                self.poly = pp
                self.mhz = round(f0 + (Li + pos) * df, 6)
                self.dbm = pp(pos)

    #--------------------------------------------------------------------------
    # Find frequency corresponding to a given vValue in given trace's data.
    #
    # trace:    trace with v[] and Fmhz[].
    # jP:       index of peak, where to start search.
    # signP:    +1 for P+, -1 for P-.
    # searchR:  True to search to right.
    # value:    vValue to search for.
    # show:     print debug lines

    def FindValue(self, trace, jP, isLogF, signP, searchR, value, show=False):
        Fmhz = (trace.Fmhz, trace.LFmhz)[isLogF]

        # trim vals and Fmhz arrays to the subset of steps to search in
        vals = trace.v
        if searchR:
            inc = 1
            r = range(jP,len(trace.v))
#            vals  = trace.v[jP:]
#            Fmhz =       Fmhz[jP:]
        else:
            inc = -1
            r = range(jP,0,-1)
#            vals  = trace.v[jP::-1]
#            Fmhz =       Fmhz[jP::-1]

        # using interpolation, locate exact freq where vals crosses value
        # (multiplied by slope to insure that vals are in increasing order)
#        slope = -signP
#        if show:
#            print ("FindValue: jP=", jP, "signP=", signP, \
#                "vals=", slope*vals[:10].round(3))
#            print (self.name, "searchR=", searchR, "value=", slope*value, \
#                "Fmhz=", Fmhz[:10].round(6))

        mhz = Fmhz[jP]
        found = False
        if signP > 0:
            for i in r:
                tmp = vals[i]
                if tmp < value:
                    found = True
                    break
        else:
            for i in r:
                tmp = vals[i]
                if tmp > value:
                    found = True
                    break
        if found:
            vl = vals[i - inc]
            fl = Fmhz[i - inc]
            mhz = (Fmhz[i] - fl) * ((value - vl) / (tmp - vl)) + fl
            if prt:
                fil = open("marker.txt","a")
                fil.write("jp %5d value %6.2f search %s\n" % (jP, value, searchR))
                fil.write("i  %5d inc %2d\n" % (i, inc))
                fil.write("vi %6.2f  vl %6.2f\n" % (tmp, vl))
                fil.write("fi %10.6f fl %10.6f\n" % (Fmhz[i], fl))
                fil.write("(Fmhz[i] - fl) %10.6f (value - vl) %8.4f (tmp - vl) %8.4f fl %10.6f\n" % \
                          ((Fmhz[i] - fl), (value - vl), (tmp - vl), fl))
                ival = interp(mhz, Fmhz, vals)
                self.mhz = mhz
                tval = self.TraceValue(trace, isLogF)
                fil.write("mhz %10.6f ival %6.2f tval %6.2f\n\n" % (mhz, ival, tval))
                fil.close();

#        mhz = interp(slope*value, slope*vals, Fmhz)
        if isLogF:
            mhz = 10**mhz

        # round to Hz
        # self.mhz = round(mhz, 6)
        self.mhz = mhz
        if show:
            print ("FindValue got mhz=", self.mhz)

    #--------------------------------------------------------------------------
    # Get value of given trace at marker's frequency.

    def TraceValue(self, trace, isLogF):
        if not trace:
            return None
        Fmhz = (trace.Fmhz, trace.LFmhz)[isLogF]
        mhz = (self.mhz, log10(max(self.mhz, 1e-6)))[isLogF]
        leftMHz = Fmhz[0]
        value = None
        if leftMHz == Fmhz[-1]:
            # if scan has zero span and marker is on center, use any value
            if mhz == leftMHz:
                value = trace.v[0]
        else:
            # normal span: interpolate between trace values
            value = interp(mhz, Fmhz, trace.v)
        if isnan(value):
            value = 0
        return value

    #--------------------------------------------------------------------------
    # Set marker's mag (& phase) by reading given traces at marker's frequency.

    def SetFromTrace(self, trace, isLogF):
        traceP = trace.phaseTrace
        if not traceP and trace.magTrace:
            trace = trace.magTrace
            traceP = trace.phaseTrace
        Fmhz = (trace.Fmhz, trace.LFmhz)[isLogF]
        mhz = (self.mhz, log10(max(self.mhz, 1e-6)))[isLogF]
        leftMHz = Fmhz[0]
        if leftMHz == Fmhz[-1]:
            # if scan has zero span and marker is on center, use any value
            if mhz == leftMHz:
                self.dbm = trace.v[0]
                if traceP:
                    self.deg = traceP.v[0]
        else:
            # normal span: interpolate between trace values
            self.dbm = interp(mhz, Fmhz, trace.v)
            if traceP:
                self.deg = interp(mhz, Fmhz, traceP.v)
        if isnan(self.dbm):
            self.dbm = 0
        if traceP and isnan(self.deg):
            self.deg = 0

    #--------------------------------------------------------------------------
    # Save a marker's mhz and traceName preferences to p.

    def SavePrefs(self, p):
        print ("name=", self.name)
        name = re.sub("\+", "p", re.sub("-", "m", self.name))
        for attr in ("mhz", "traceName"):
            setattr(p, "markers_"+name+"_"+attr, getattr(self, attr))

#==============================================================================
# A window showing important variables.

class VarDialog(wx.Dialog):
    def __init__(self, frame):
        self.frame = frame
        self.prefs = p = frame.prefs
        framePos = frame.GetPosition()
        #frameSize = frame.GetSize()
        pos = p.get("varWinPos", (frame.screenWidth-200, framePos.y))
        textList = msa.GetVarsTextList()
        size = (200, 40 + (fontSize+6)*(len(textList)))
        wx.Dialog.__init__(self, frame, -1, "Variables", pos,
                            size, wx.DEFAULT_DIALOG_STYLE)
        self.Bind(wx.EVT_PAINT,     self.OnPaint)
        self.Bind(wx.EVT_MOVE,     self.OnMove)
        self.SetBackgroundColour(p.theme.backColor)
        self.Show()

    def OnPaint(self, event):
        dc = wx.PaintDC(self)
        p = self.prefs
        textList = msa.GetVarsTextList()
        coords = [(10, 5+(fontSize+6)*i) for i in range(len(textList))]
        dc.SetTextForeground(p.theme.foreColor)
        dc.SetFont(wx.Font(fontSize-1, wx.SWISS, wx.NORMAL, wx.NORMAL))
        dc.DrawTextList(textList, coords)

    def OnMove(self, event):
        self.prefs.varWinPos = self.GetPosition().Get()

    def OnClose(self, event):
        self.Destroy()
        self.frame.varDlg = None


#==============================================================================
# Base class for main dialog boxes.

class MainDialog(wx.Dialog):

    # Add a test-fixture settings box to the dialog. Returns its sizer.

    def FixtureBox(self, isSeriesFix=True, isShuntFix=False):
        p = self.frame.prefs
        c = wx.ALIGN_CENTER
        chb = wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_BOTTOM
        self.R0 = p.get("R0", 50.)
        titleBox = wx.StaticBox(self, -1, "Test Fixture")
        sizerHF = wx.StaticBoxSizer(titleBox, wx.HORIZONTAL)
        sizerVF1 = wx.BoxSizer(wx.VERTICAL)
        rb = wx.RadioButton(self, -1, "Series", style= wx.RB_GROUP)
        self.seriesRB = rb
        rb.SetValue(isSeriesFix)
        sizerVF1.Add(rb, 0, wx.ALL, 2)
        self.shuntRB = rb = wx.RadioButton(self, -1, "Shunt")
        rb.SetValue(isShuntFix)
        sizerVF1.Add(rb, 0, wx.ALL, 2)
        if msa.mode == MSA.MODE_VNARefl:
            self.bridgeRB = rb = wx.RadioButton(self, -1, "Bridge")
            rb.SetValue(not (isSeriesFix or isShuntFix))
            sizerVF1.Add(rb, 0, wx.ALL, 2)
        sizerHF.Add(sizerVF1, 0, c|wx.RIGHT, 10)
        sizerVG2 = wx.GridBagSizer()
        sizerVG2.Add(wx.StaticText(self, -1, "R0"), (0, 0), flag=chb)
        self.R0Box = tc = wx.TextCtrl(self, -1, gstr(self.R0), size=(40, -1))
        sizerVG2.Add(tc, (1, 0), flag=c)
        sizerVG2.Add(wx.StaticText(self, -1, Ohms), (1, 1),
                flag=c|wx.LEFT, border=4)
        sizerHF.Add(sizerVG2, 0, c)
        return sizerHF


#==============================================================================
# A Help modal dialog for  dialog. # JGH

class ConfigHelpDialog(wx.Dialog):
    def __init__(self, frame):
        p = frame.prefs
        pos = p.get("configHelpWinPos", wx.DefaultPosition)
        title = "Configuration Manager Help"
        wx.Dialog.__init__(self, frame, -1, title, pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        sizerV = wx.BoxSizer(wx.VERTICAL)
        self.SetBackgroundColour("WHITE")
        text = "Enter configuration data for your machine. "\
        "With a standard SLIM build, the items in WHITE likely need no "\
        "change. CYAN items and Auto Switch checkboxes generally must be "\
        "customized."
        self.st = st = wx.StaticText(self, -1, text, pos=(10, 10))

        st.Wrap(600)
        sizerV.Add(st, 0, wx.ALL, 5)

        # OK button
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        butSizer.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, wx.ID_OK)
        btn.SetDefault()
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(butSizer, 0, wx.ALIGN_RIGHT)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if pos == wx.DefaultPosition:
            self.Center()

#==============================================================================
# The MSA/VNA Configuration Manager dialog box (also modal) # JGH

class ConfigDialog(wx.Dialog): # JGH Heavily modified 1/20/14
    def __init__(self, frame):
        self.frame = frame
        self.prefs = p = frame.prefs
        pos = p.get("configWinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1, "MSA/VNA Configuration Manager",
                             pos, wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)

        c = wx.ALIGN_CENTER
        cv = wx.ALIGN_CENTER_VERTICAL
        chbt = wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_BOTTOM|wx.TOP
        bigFont = wx.Font(16, wx.SWISS, wx.NORMAL, wx.NORMAL)
        sizerV0 = wx.BoxSizer(wx.VERTICAL)
        text = wx.StaticText(self, -1, "ENTER CONFIGURATION DATA FOR YOUR MSA")
        text.SetFont(bigFont)
        sizerV0.Add(text, 0, flag=c)
        sizerH0 = wx.BoxSizer(wx.HORIZONTAL)

        # PLL and DDS config
        sizerG1 = wx.GridBagSizer(hgap=10, vgap=2)  # Sizer for first column
        for i in range(3):
            text = wx.StaticText(self, -1, "PLL%d" % (i+1))
            text.SetFont(bigFont)
            sizerG1.Add(text, (0, i), flag=c)
        st = wx.StaticText(self, -1,  "-------------Type--------------" )
        sizerG1.Add(st, (1, 0), (1, 3), chbt, 5)
        st = wx.StaticText(self, -1, "---Is this a passive PLL Loop?---")
        sizerG1.Add(st, (3, 0), (1, 3), chbt, 5)
        st = wx.StaticText(self, -1, "------Phase frequency (MHz)------")
        sizerG1.Add(st, (5, 0), (1, 3), chbt, 5)
        st = wx.StaticText(self, -1, "DDS1----Center Freq (MHz)----DDS3")
        sizerG1.Add(st, (7, 0), (1, 3), chbt, 5)
        st = wx.StaticText(self, -1, "DDS1-----Bandwidth (MHz)------DDS3")
        sizerG1.Add(st, (9, 0), (1, 3), chbt, 5)

        csz = (95, -1) # JGH
        tsz = (95, -1) # JGH

        #JGH added . Modified 2/14/14
        pllTypeChoices = ["2325", "2326", "2350", "2353",\
                      "4112", "4113", "4118"]
        s = p.get("PLL1type", pllTypeChoices[1])   # Default value
        cmPLL1 = wx.ComboBox(self, -1, s, (0, 0), csz, choices=pllTypeChoices,
                             style=wx.CB_READONLY)
        cmPLL1.Enable(True)
        self.cmPLL1 = cmPLL1
        sizerG1.Add(cmPLL1, (2, 0), flag=c)

        s = p.get("PLL2type", pllTypeChoices[1])
        cmPLL2 = wx.ComboBox(self, -1, s, (0, 0), csz, choices=pllTypeChoices,
                             style=wx.CB_READONLY)
        cmPLL2.Enable(True)
        self.cmPLL2 = cmPLL2
        sizerG1.Add(cmPLL2, (2, 1), flag=c)

        s = p.get("PLL3type", pllTypeChoices[1])
        cmPLL3 = wx.ComboBox(self, -1, s, (0, 0), csz, choices=pllTypeChoices,
                             style=wx.CB_READONLY)
        cmPLL3.Enable(True)
        self.cmPLL3 =cmPLL3
        sizerG1.Add(cmPLL3, (2, 2), flag=c)

        pllPolInvChoices = [" 0 : No", " 1 : Yes"]
        s = p.get("PLL1phasepol", 0)
        s = pllPolInvChoices[s]
        cmPOL1 = wx.ComboBox(self, -1, s, (0, 0), csz,
                             choices=pllPolInvChoices, style=wx.CB_READONLY)
        cmPOL1.Enable(True)
        self.cmPOL1 = cmPOL1
        sizerG1.Add(cmPOL1, (4, 0), flag=c)

        s = p.get("PLL2phasepol", 1)
        s = pllPolInvChoices[s]
        cmPOL2 = wx.ComboBox(self, -1, s, (0, 0), csz,
                             choices=pllPolInvChoices, style=wx.CB_READONLY)
        cmPOL2.Enable(True)
        self.cmPOL2 = cmPOL2
        sizerG1.Add(cmPOL2, (4, 1), flag=c)

        s = p.get("PLL3phasepol", 0)
        s = pllPolInvChoices[s]
        cmPOL3 = wx.ComboBox(self, -1, s, (0, 0), csz,
                             choices=pllPolInvChoices, style=wx.CB_READONLY)
        cmPOL3.Enable(True)
        self.cmPOL3 = cmPOL3
        sizerG1.Add(cmPOL3, (4, 2), flag=c)

        s = p.get("PLL1phasefreq", 0.974)
        tcPhF1 = wx.TextCtrl(self, -1, gstr(s), size=tsz)
        tcPhF1.Enable(True)
        self.tcPhF1 = tcPhF1
        sizerG1.Add(tcPhF1, (6, 0), flag=c)

        s = p.get("PLL2phasefreq", 4.000) # JGH 2/15/14
        tcPhF2 = wx.TextCtrl(self, -1, gstr(s), size=tsz)
        tcPhF2.Enable(True)
        self.tcPhF2 = tcPhF2
        sizerG1.Add(tcPhF2, (6, 1), flag=c)

        s = p.get("PLL3phasefreq", 0.974)
        tcPhF3 = wx.TextCtrl(self, -1, gstr(s), size=tsz)
        tcPhF3.Enable(True)
        self.tcPhF3 = tcPhF3
        sizerG1.Add(tcPhF3, (6, 2), flag=c)

        # JGH 2/15/14: PLL mode no longer used

        cvl = wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_LEFT
        #cvr = wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_RIGHT
        # JGH addition end

        tc = wx.TextCtrl(self, -1, gstr(LO1.appxdds), size=tsz) # JGH 2/2/14
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        tc.Enable(True)
        self.dds1CentFreqBox = tc
        sizerG1.Add(tc, (8, 0), flag=c)

        tc = wx.TextCtrl(self, -1, gstr(LO3.appxdds), size=tsz) # JGH 2/2/14
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        tc.Enable(True)
        self.dds3CentFreqBox = tc
        sizerG1.Add(tc, (8, 2), flag=c)

        tc = wx.TextCtrl(self, -1, gstr(LO1.ddsfilbw), size=tsz) # JGH 2/2/14
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        tc.Enable(True)
        self.dds1BWBox = tc
        sizerG1.Add(tc, (10, 0), flag=c)

        tc = wx.TextCtrl(self, -1, gstr(LO3.ddsfilbw), size=tsz) # JGH 2/2/14
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        tc.Enable(True)
        self.dds3BWBox = tc
        sizerG1.Add(tc, (10, 2), flag=c)


        st = wx.StaticText(self, -1, "DDS1 Parser")
        sizerG1.Add(st, (11, 0), (1, 1), chbt, 10)
        s = p.get("dds1Parser", "16bit serial")
        dds1Parser = wx.ComboBox(self, -1, s, (0, 0), csz, [s])
        dds1Parser.Enable(True)
        self.dds1Parser = dds1Parser
        sizerG1.Add(dds1Parser, (12, 0), flag=c)

        st = wx.StaticText(self, -1, "LO2 (MHz)")
        sizerG1.Add(st, (11, 1), (1, 1), chbt, 10)
        s = p.get("appxLO2", 1024)
        tc = wx.TextCtrl(self, -1, gstr(s), size=tsz) # JGH 2/2/14
        tc.Enable(True)
        self.appxLO2 = tc
        sizerG1.Add(tc, (12, 1), flag=c)

        st = wx.StaticText(self, -1, "Mast Clk (MHz)")
        sizerG1.Add(st, (11, 2), (1, 1), chbt, 10)
        s = p.get("masterclock", 64)
        mastClkBox = wx.TextCtrl(self, -1, gstr(s), size=tsz) # JGH 2/2/14
        mastClkBox.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        mastClkBox.Enable(True)
        self.mastClkBox = mastClkBox
        sizerG1.Add(mastClkBox, (12, 2), flag=c)

        st = wx.StaticText(self, -1,  "Max PDM out" )
        sizerG1.Add(st, (13, 0), (1, 1), chbt, 10)
        maxPDMout = wx.TextCtrl(self, -1, gstr(2**16-1), size=tsz) # JGH 2/2/14
        maxPDMout.Enable(True) # JGH changed to True
        self.maxPDMout = maxPDMout
        sizerG1.Add(maxPDMout, (14, 0), flag=c)

        st = wx.StaticText(self, -1,  "Sig Gen MHz" ) # JGH added 2/15/14
        sizerG1.Add(st, (13, 1), (1, 1), chbt, 10)
        sigGenBox = wx.TextCtrl(self, -1, gstr(msa._sgout), size=tsz)
        sigGenBox.Enable(True)
        self.sigGenBox = sigGenBox
        sizerG1.Add(sigGenBox, (14, 1), flag=c)

        st = wx.StaticText(self, -1,  "Inv Deg" )
        sizerG1.Add(st, (13, 2), (1, 1), chbt, 10)
        s = p.get("invDeg", 180)
        invDegBox = wx.TextCtrl(self, -1, gstr(s), size=tsz) # JGH 2/2/14
        invDegBox.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        invDegBox.Enable(True)
        self.invDegBox = invDegBox
        sizerG1.Add(invDegBox, (14, 2), flag=c)

        # Cancel and OK buttons

        self.helpBtn = btn = wx.Button(self, -1, "Help")
        btn.Bind(wx.EVT_BUTTON, self.OnHelp)
        sizerG1.Add(btn, (16,0), flag=c)
        btn = wx.Button(self, wx.ID_CANCEL)
        sizerG1.Add(btn, (16,1), flag=c)
        btn = wx.Button(self, wx.ID_OK)
        btn.SetDefault()
        sizerG1.Add(btn, (16,2), flag=c)

        sizerH0.Add(sizerG1, 0, wx.ALL, 10)
        sizerV2 = wx.BoxSizer(wx.VERTICAL) # DEFINE SECOND COLUMN SIZER
        sizerH2 = wx.BoxSizer(wx.HORIZONTAL)
        # Final RBW Filter config
        self.rbwFiltersTitle = \
                wx.StaticBox(self, -1, "Final RBW Filters" ) #JGH 12/25/13
        sizerV2A = wx.StaticBoxSizer(self.rbwFiltersTitle, wx.VERTICAL)

        colLabels = ["Freq(MHz)", "BW(kHz)"]
        self.gridRBW = gr = wx.grid.Grid(self)
        gr.CreateGrid(4,2)
        for col in range(2):
            gr.SetColLabelValue(col, colLabels[col])
        gr.SetRowLabelSize(35)
        for i, (freq, bw) in enumerate(msa.RBWFilters):
            gr.SetCellValue(i, 0, "%2.6f" % freq) # Jgh 1/28/14
            gr.SetCellValue(i, 1, "%3.1f" % bw)
        gr.SetDefaultCellAlignment(wx.ALIGN_RIGHT, wx.ALIGN_CENTRE)
        gr.EnableEditing(1)
        sizerV2A.Add(gr, 0, wx.ALIGN_CENTER) # JGH 1/28/14
##      The next two lines might be needed later
##        gr.Bind(wx.grid.EVT_GRID_SELECT_CELL, self.OnRBWCellSel)
##        gr.Bind(wx.grid.EVT_GRID_LABEL_LEFT_CLICK, self.OnRBWLabelSel)

        sizerH2.Add(sizerV2A, 0, wx.ALL|wx.EXPAND, 5)

        # Video Filters config

        self.vidFiltBoxTitle = \
            wx.StaticBox(self, -1, "Video Filters")
        sizerV2B = wx.StaticBoxSizer(self.vidFiltBoxTitle, wx.VERTICAL)

        colLabels = "(%sF)" % mu
        rowLabels = msa.vFilterNames
        self.gridVF = gv = wx.grid.Grid(self)
        gv.CreateGrid(4,1)
        for (i, uFcap) in enumerate(msa.vFilterCaps): # JGH 2/22/14
            gv.SetCellValue(i, 0, "%4.3f" % uFcap) # JGH 2/22/14
        gv.SetRowLabelSize(72)
        gv.SetDefaultColSize(64)
        gv.SetColLabelValue(0, colLabels)
        for row in range(4):
            gv.SetRowLabelValue(row, rowLabels[row])
        gv.SetDefaultCellAlignment(wx.ALIGN_RIGHT, wx.ALIGN_CENTRE)
        gv.EnableEditing(1)
        sizerV2B.Add(gv, 1, flag=cv)

        sizerH2.Add(sizerV2B, 0, wx.ALL|wx.EXPAND, 4)
        sizerV2.Add(sizerH2, 0)

        # Optional Modules
        optModsTitle = \
                wx.StaticBox(self, -1, "Optional Modules" ) #JGH 3/3/14
        sizerH3 = wx.StaticBoxSizer(optModsTitle, wx.HORIZONTAL)
        sizerV3A = wx.BoxSizer(wx.VERTICAL)
        st = wx.StaticText(self, -1, "Available Mods")
        sizerV3A.Add(st, 0, flag=c)
        availModList = wx.ListBox(self, -1, pos=wx.DefaultPosition, \
                                  size=(120,120), choices=['DUTatten', 'SyntDUT'], \
                                  style=wx.LB_ALWAYS_SB|wx.LB_SINGLE)
        sizerV3A.Add(availModList, 1, flag=c)
        sizerH3.Add(sizerV3A, 0)
        sizerV3B = wx.BoxSizer(wx.VERTICAL)
        mrBtn = wx.Button(self, -1, ">>")
        mrBtn.Bind(wx.EVT_BUTTON, self.OnMoveRight)
        sizerV3B.Add(mrBtn, 0, flag=c)
        mlBtn = wx.Button(self, -1, "<<")
        mlBtn.Bind(wx.EVT_BUTTON, self.OnMoveLeft)
        sizerV3B.Add(mlBtn, 0, flag=c)
        sizerH3.Add(sizerV3B, 1, flag=wx.ALIGN_CENTER_VERTICAL)
        sizerV3C = wx.BoxSizer(wx.VERTICAL)
        st = wx.StaticText(self, -1, "Imported Mods")
        sizerV3C.Add(st, 0, flag=c)
        importModList = wx.ListBox(self, -1, pos=wx.DefaultPosition, \
                                  size=(120,120), choices="", style=wx.LB_ALWAYS_SB|wx.LB_SINGLE)
        sizerV3C.Add(importModList, 1, flag=c)
        sizerH3.Add(sizerV3C, 2)
        sizerV2.Add(sizerH3, 0)
        
        # TOPOLOGY

        self.topologyBoxTitle = wx.StaticBox(self, -1, "Topology")
        sizerV2C = wx.StaticBoxSizer(self.topologyBoxTitle, wx.VERTICAL)

        sizerG2B = wx.GridBagSizer(hgap=4, vgap=2)
        cwsz = (120, -1)

        sizerG2B.Add(wx.StaticText(self, -1,  "ADC type" ), (0, 0), flag=cvl)
        ADCoptions = ["16bit serial", "12bit serial", "12bit ladder"]
        s = p.get("ADCtype", ADCoptions[0])
        cm = wx.ComboBox(self, -1, s, (0, 0), cwsz, style=wx.CB_READONLY)
        cm.Enable(True)
        self.ADCoptCM = cm
        sizerG2B.Add(cm, (0, 1), flag=cv)

        sizerG2B.Add(wx.StaticText(self, -1,  "Interface" ), (1, 0), flag=cvl)
        
        if isWin:
            CBoptions = ['LPT', 'USB', 'RPI', 'BBB']
            s = p.get("CBopt", CBoptions[1])
        else:
            CBoptions = ['USB', 'RPI', 'BBB'] # JGH 1/16/14
            s = p.get("CBopt", CBoptions[0])
        cm = wx.ComboBox(self, -1, s, (0, 0), cwsz, choices=CBoptions, style=wx.CB_READONLY)
        cm.Enable(True)
        sizerG2B.Add(cm, (1, 1), flag=cv)
        self.CBoptCM = cm
        sizerV2C.Add(sizerG2B, 0, wx.ALL, 5)

        sizerV2.Add(sizerV2C, 0, wx.ALL|wx.EXPAND, 4)
        sizerH0.Add(sizerV2, 0, wx.ALIGN_TOP)

        # JGH add end

        sizerV0.Add(sizerH0, 0, wx.ALL, 10)

        self.SetSizer(sizerV0)
        sizerV0.Fit(self)
        if pos == wx.DefaultPosition:
            self.Center()

    #--------------------------------------------------------------------------
    # Module directory
    def CreateModDir(self):
    
        directory = os.path.join(appdir, "MSA_Mods")
        if not os.path.exists(directory):
            os.makedirs(directory)
        return directory

    def OnMoveRight(self, event=None):
        pass

    def OnMoveLeft(self, event=None):
        pass

    def OnModOK(self, event=None):
        pass

    #--------------------------------------------------------------------------
    # Present Help dialog.

    def OnHelp(self, event):
        self.helpDlg = dlg = ConfigHelpDialog(self)
        dlg.Show()
        # JGH added 1/21/14
        result = dlg.ShowModal()
        if (result == wx.ID_OK):
            dlg.Close()
        # JGH ends 1/21/14

    #--------------------------------------------------------------------------
    # Cancel actions
    def OnCancel(self, event):
        pass

    #--------------------------------------------------------------------------
    # Focus on a text box: select contents when tabbed to for easy replacement.

    def OnSetFocus(self, event):
        if isMac:
            tc = event.GetEventObject()
            tc.SelectAll()
        event.Skip()

    #--------------------------------------------------------------------------
    # Handle Final Filter ListCtrl item addition/deletion.
    # JGH This section deleted on its entirety 1/21/14

#==============================================================================
# Calibration File Utilities.

# Make a calibration file name from a path number, returning (directory, fileName).
# Also creates the MSA_Info/MSA_Cal dirs if needed.

def CalFileName(pathNum):
    if pathNum == 0:
        fileName = "MSA_CalFreq.txt"
    else:
        fileName = "MSA_CalPath%d.txt" % pathNum
    directory = os.path.join(appdir, "MSA_Info", "MSA_Cal")
    if not os.path.exists(directory):
        os.makedirs(directory)
    return directory, fileName

# Check the version of a calibration file.

def CalCheckVersion(fName): # JGH 2/9/14
    f = open(fName, "Ur")   # JGH 2/9/14
    for i in range(3):
        line = f.readline()
    if line.strip() != "CalVersion= %s" % CalVersion:
        raise ValueError("File %s is the wrong version. Need %s" % \
                    (fName, CalVersion))    # JGH 2/9/14

# Parse a Mag calibration file, returning adc, Sdb, Sdeg arrays.

def CalParseMagFile(fName): # JGH 2/9/14
    for i in range(5):
        fName.readline()    # JGH 2/9/14
    Madc = []; Sdb = []; Sdeg = []
    for line in fName.readlines():  # JGH 2/9/14
        words = map(string.strip, line.split())
        if len(words) == 3:
            Madc.append(int(words[0]))
            Sdb.append(float(words[1]))
            Sdeg.append(float(words[2]))
    return Madc, Sdb, Sdeg

# Parse a Freq calibration file, returning freq, db arrays.

def CalParseFreqFile(fName):    # JGH 2/9/14
    # (genfromtxt in Python2.6 only)
    ##data = genfromtxt(file, dtype=[("freq", "f8"), ("db", "f8")], \
    ##        comments="*", skip_header=5)
    ##return data["freq"], data["db"]
    for i in range(5):
        fName.readline()    # JGH 2/9/14
    Fmhz = []; dbs = []
    for line in fName.readlines():  # JGH 2/9/14
        words = map(string.strip, line.split())
        if len(words) == 2:
            Fmhz.append(float(words[0]))
            dbs.append(float(words[1]))
    return Fmhz, dbs

# Generate a Mag calibration file.

def CalGenMagFile(fName, Madc, Sdb, Sdeg, pathNum, freq, bw, calFreq):  # JGH 2/9/14
    fName.write( \
        "*Filter Path %d: CenterFreq=%8.6f MHz; Bandwidth=%8.6f KHz\n"\
        "*Calibrated %s at %8.6f MHz.\n"\
        "CalVersion= %s\n"\
        "MagTable=\n"\
        "*  ADC      dbm      Phase   in increasing order of ADC\n" % \
        (pathNum, freq, bw, time.strftime("%D"), calFreq, CalVersion))
    for fset in sorted(zip(Madc, Sdb, Sdeg)):
        fName.write("%6i %9.3f %8.2f\n" % fset)  # JGH 2/9/14
    fName.close()   # JGH 2/9/14

# Generate a Freq calibration file.

def CalGenFreqFile(fName, Fmhz, dbs, calDbm):   # JGH 2/9/14
    fName.write( \
        "*Calibration over frequency\n"\
        "*Calibrated %s at %8.3f dbm.\n"\
        "CalVersion= %s\n"\
        "FreqTable=\n"\
        "*    MHz        db   in increasing order of MHz\n" % \
        (time.strftime("%D"), calDbm, CalVersion))
    for fset in sorted(zip(Fmhz, dbs)):
        fName.write("%11.6f %9.3f\n" % fset) # JGH 2/9/14
    fName.close()


#==============================================================================
# The Calibration File Manager dialog box.

class CalManDialog(wx.Dialog):
    def __init__(self, frame):
        self.frame = frame
        if msa.IsScanning():
            msa.StopScan()
        self.prefs = p = frame.prefs
        pos = p.get("calManWinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1, "Calibration File Manager", pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        c = wx.ALIGN_CENTER
        self.sizerV = sizerV = wx.BoxSizer(wx.VERTICAL)
        sizerH1 = wx.BoxSizer(wx.HORIZONTAL)

        # file editor box
        sizerV2 = wx.BoxSizer(wx.VERTICAL)
        st = wx.StaticText(self, -1, "Path Calibration Table" )
        sizerV2.Add(st, 0, flag=c)
        self.editBox = tc = wx.TextCtrl(self, -1, "", size=(350, 300), \
                style=wx.TE_MULTILINE|wx.HSCROLL|wx.VSCROLL) # JGH 1/31/14
        tc.SetFont(wx.Font(fontSize*1.2, wx.TELETYPE, wx.NORMAL, wx.NORMAL))
        tc.Bind(wx.EVT_CHAR, self.OnTextEdit)
        sizerV2.Add(tc, 0, c|wx.ALL, 5)

        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.cleanupBtn = btn = wx.Button(self, -1, "Clean Up")
        btn.Bind(wx.EVT_BUTTON, self.OnCleanUp)
        butSizer.Add(btn, 0, wx.ALL, 5)
        self.defaultsBtn = btn = wx.Button(self, -1, "Display Defaults")
        btn.Bind(wx.EVT_BUTTON, self.OnSetDefaults)
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV2.Add(butSizer, 0, c)
        sizerH1.Add(sizerV2, 0, wx.EXPAND|wx.ALL, 20)

        # files chooser
        sizerV3 = wx.BoxSizer(wx.VERTICAL)
        sizerV3.Add(wx.StaticText(self, -1,  "Available Files" ), 0, flag=c)
        self.filesListCtrl = lc = wx.ListCtrl(self, -1, (0, 0), (180, 160),
            wx.LC_REPORT|wx.LC_SINGLE_SEL)
        lc.InsertColumn(0, "File")
        lc.InsertColumn(1, "Freq")
        lc.InsertColumn(2, "BW")
        lc.SetColumnWidth(0, 35)
        lc.SetColumnWidth(1, 90)
        lc.SetColumnWidth(2, 40)
        lc.InsertStringItem(0, "")
        lc.SetStringItem(0, 0, gstr(0))
        lc.SetStringItem(0, 1, "(Frequency)")

        i = 1
        for freq, bw in msa.RBWFilters:
            lc.InsertStringItem(i, "")
            lc.SetStringItem(i, 0, gstr(i))
            lc.SetStringItem(i, 1, gstr(freq))
            lc.SetStringItem(i, 2, gstr(bw))
            i += 1

        self.pathNum = None
        lc.Bind(wx.EVT_LIST_ITEM_SELECTED, self.OnFileItemSel)
        lc.MoveBeforeInTabOrder(self.editBox)
        sizerV3.Add(lc, 0, c|wx.ALL, 5)
        sizerH1.Add(sizerV3, 1, wx.EXPAND|wx.ALL, 20)
        sizerV.Add(sizerH1, 0, c)

        # instructions and measurement controls
        self.sizerG1 = sizerG1 = wx.GridBagSizer(hgap=10, vgap=2)
        self.startBtn = btn = wx.Button(self, -1, "Start Data Entry")
        btn.Bind(wx.EVT_BUTTON, self.OnStartBtn)
        sizerG1.Add(btn, (0, 0), flag=c)
        sizerV.Add(sizerG1, 0, c|wx.ALL, 5)

        self.beginText = \
            "To begin entry of calibration data, click Start Entry.\n"\
            "Alternatively, you may enter, alter and delete data in "\
            "the text editor.\n"
        self.instructBox = text = wx.TextCtrl(self, -1, self.beginText,
            size=(600, 180),
            style=wx.TE_READONLY|wx.NO_BORDER|wx.TE_MULTILINE)
        text.SetBackgroundColour(wx.WHITE)
        sizerV.Add(text, 1, c|wx.ALL, 5)

        # Cancel and OK buttons
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        butSizer.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, wx.ID_CANCEL)
        butSizer.Add(btn, 0, wx.ALL, 5)
        btn = wx.Button(self, wx.ID_OK)
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(butSizer, 0, wx.ALIGN_RIGHT|wx.ALIGN_BOTTOM)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if pos == wx.DefaultPosition:
            self.Center()

        self.calDbm = 0.
        self.dirty = False
        self.cancelling = False
        self.refPhase = 0.
        self.refFreq = msa._fStart
        lc.SetItemState(0, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)

    #--------------------------------------------------------------------------
    # A character was typed in the text edit box. Say it's now modified.

    def OnTextEdit(self, event):
        self.dirty = True
        self.cleanupBtn.Enable(True)
        self.defaultsBtn.Enable(True)
        event.Skip()

    #--------------------------------------------------------------------------
    # Clean up the formatting of the current calibration text by parsing and
    # then re-generating it.

    def OnCleanUp(self, event):
        lc = self.filesListCtrl
        i = self.pathNum
        tc = self.editBox
        fin = StringIO(tc.GetValue())
        fout = StringIO("")
        if i == 0:
            pass
            ##Fmhz, dbs = CalParseFreqFile(fin)
            ##CalGenFreqFile(fout, Fmhz, dbs, self.calDbm)
        else:
            centerFreq = float(lc.GetItem(i, 1).GetText())
            bw =         float(lc.GetItem(i, 2).GetText())
            Madc, Sdb, Sdeg = CalParseMagFile(fin)
            CalGenMagFile(fout, Madc, Sdb, Sdeg, i, centerFreq, bw,
                            self.refFreq)
        tc.SetValue(string.join(fout.buflist))
        self.dirty = True
        self.cleanupBtn.Enable(False)

    #--------------------------------------------------------------------------
    # Set the calibration table to default values.

    def OnSetDefaults(self, event):
        if self.dirty:
            if self.SaveIfAllowed(self) == wx.ID_CANCEL:
                return
        #lc = self.filesListCtrl
        i = self.pathNum
        tc = self.editBox
        fout = StringIO("")
        if i == 0:
            Fmhz = [0., 1000.]
            dbs = [0., 0.]
            CalGenFreqFile(fout, Fmhz, dbs, self.calDbm)
        else:
            Madc = [0, 32767]
            Sdb = [-120., 0.]
            Sdeg = [0., 0.]
            CalGenMagFile(fout, Madc, Sdb, Sdeg, i, 10.7, 8, self.refFreq)
        tc.SetValue(string.join(fout.buflist))
        self.dirty = True
        self.cleanupBtn.Enable(False)

    #--------------------------------------------------------------------------
    # Save the modified text to the file, if confirmed. May return
    # wx.ID_CANCEL.

    def SaveIfAllowed(self, parent):
        dlg = wx.MessageDialog(parent, "Unsaved calibration changes will be "\
                "lost. Do you want to SAVE first?", \
                "Warning", style=wx.YES_NO|wx.CANCEL|wx.CENTER)
        answer = dlg.ShowModal()
        if answer == wx.ID_YES:
            directory, fileName = CalFileName(self.pathNum)   # JGH 2/9/14
            wildcard = "Text (*.txt)|*.txt"
            while True:
                dlg = wx.FileDialog(self, "Save file as...", defaultDir=directory,
                        defaultFile=fileName, wildcard=wildcard, style=wx.SAVE) # JGH 2/9/14
                answer = dlg.ShowModal()
                if answer != wx.ID_OK:
                    break
                path = dlg.GetPath()
                if ShouldntOverwrite(path, parent):
                    continue
                f = open(path, "w")
                f.write(self.editBox.GetValue())
                f.close()
                print ("Wrote configuration to", path)
                self.dirty = False
                break
        return answer

    #--------------------------------------------------------------------------
    # Handle a calibration file selection.

    def OnFileItemSel(self, event):
        if self.cancelling:
            self.cancelling = False
            return
        i = event.m_itemIndex

        # save current file first, if it needs it
        if self.dirty and i != self.pathNum:
            if self.SaveIfAllowed(self) == wx.ID_CANCEL:
                # canelled: undo selection change
                self.cancelling = True
                lc = self.filesListCtrl
                lc.SetItemState(i, 0, wx.LIST_STATE_SELECTED)
                lc.SetItemState(self.pathNum, wx.LIST_STATE_SELECTED,
                    wx.LIST_STATE_SELECTED)
                return

        # open newly selected file
        self.pathNum = i
        try:
            directory, fileName = CalFileName(i)    # JGH 2/9/14
            text = open(os.path.join(directory, fileName), "Ur").read() # JGH 2/9/14
            self.editBox.SetValue(text)
            self.dirty = False
            self.cleanupBtn.Enable(False)
        except IOError:
            print ("File %s not found, using defaults." % fileName)
            self.OnSetDefaults(0)
        self.instructBox.SetValue(self.beginText)

    #--------------------------------------------------------------------------
    # Start Data Entry button.

    def OnStartBtn(self, event):
        self.instructBox.SetValue( \
        "The Spectrum Analyzer must be configured for zero sweep width. "\
        "Center Frequency must be higher than 0 MHz. The first data Point "\
        "will become the Reference data for all other data Points. Click "\
        "Measure button to display the data measurements for ADC value and "\
        "Phase. Manually, enter the Known Power Level into the Input (dBm) "\
        "box. Click the Enter button to insert the data into the Path "\
        "Calibration Table. Subsequent Data may be entered in any order, and "\
        "sorted by clicking Clean Up. ADC bits MUST increase in order and no "\
        "two can be the same. You may alter the Data in the table, or boxes, "\
        "by highlighting and retyping. The Phase Data (Phase Error vs Input "\
        "Power) = Measured Phase - Ref Phase, is Correction Factor used in "\
        "VNA. Phase is meaningless for the Basic MSA, or MSA with TG. ")
        sizerG1 = self.sizerG1
        self.startBtn.Destroy()

        c = wx.ALIGN_CENTER
        chb = wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_BOTTOM
        tsz = (90, -1)
        st = wx.StaticText(self, -1, "Input (dbm)")
        sizerG1.Add(st, (2, 0), (1, 1), chb, 0)
        self.inputBox = tc = wx.TextCtrl(self, -1, "", size=tsz)
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        sizerG1.Add(tc, (3, 0), flag=c)

        st = wx.StaticText(self, -1, "ADC value")
        sizerG1.Add(st, (2, 1), (1, 1), chb, 0)
        self.adcBox = tc = wx.TextCtrl(self, -1, "", size=tsz)
        sizerG1.Add(tc, (3, 1), flag=c)

        st = wx.StaticText(self, -1, "Phase")
        sizerG1.Add(st, (1, 2), (1, 1), chb, 0)
        st = wx.StaticText(self, -1, "(degrees)")
        sizerG1.Add(st, (2, 2), (1, 1), chb, 0)
        self.phaseBox = tc = wx.TextCtrl(self, -1, "", size=tsz)
        sizerG1.Add(tc, (3, 2), flag=c)

        btn = wx.Button(self, -1, "Measure")
        btn.Bind(wx.EVT_BUTTON, self.OnMeasure)
        btn.SetDefault()
        sizerG1.Add(btn, (3, 3), flag=c)

        btn = wx.Button(self, -1, "Enter")
        btn.Bind(wx.EVT_BUTTON, self.OnEnter)
        sizerG1.Add(btn, (3, 4), flag=c)

        st = wx.StaticText(self, -1, "Ref Freq (MHz)")
        sizerG1.Add(st, (2, 5), (1, 1), chb, 0)
        self.refFreqBox = tc = wx.TextCtrl(self, -1, "", size=tsz)
        self.refFreqBox.SetValue(gstr(self.refFreq))
        sizerG1.Add(tc, (3, 5), flag=c)
        self.sizerV.Fit(self)

        self.haveRefMeas = False
        self.inputBox.SetFocus()

    #--------------------------------------------------------------------------
    # Key focus changed.

    def OnSetFocus(self, event):
        tc = event.GetEventObject()
        if isMac:
            tc.SelectAll()
        self.tcWithFocus = tc
        event.Skip()

    #--------------------------------------------------------------------------
    # Make a measurement and update ADC and phase value boxes.

    def OnMeasure(self, event):
        msa.WrapStep()
        freq, adc, Sdb, Sdeg = msa.CaptureOneStep(post=False, useCal=False)
        self.adcBox.SetValue(gstr(adc))
        if isnan(Sdeg):
            Sdeg = 0
        self.phaseBox.SetValue("%7g" % (Sdeg - self.refPhase))
        self.refFreqBox.SetValue(gstr(freq))
        self.inputBox.SetFocus()
        if isMac:
            self.inputBox.SelectAll()

    #--------------------------------------------------------------------------
    # Enter the measurement values into the calibration table.

    def OnEnter(self, event):
        adc = int(self.adcBox.GetValue())
        Sdb = float(self.inputBox.GetValue())
        Sdeg = float(self.phaseBox.GetValue())

        # if first one, make it the reference measurement
        if not self.haveRefMeas:
            self.haveRefMeas = True
            self.refMag = Sdb
            self.refPhase = Sdeg

            sizerG1 = self.sizerG1
            c = wx.ALIGN_CENTER
            chb = wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_BOTTOM
            tsz = (90, -1)
            st = wx.StaticText(self, -1, "Ref Input (dbm)")
            sizerG1.Add(st, (0, 0), (1, 1), chb, 0)
            tc = wx.TextCtrl(self, -1, "%7g" % Sdb, size=tsz)
            self.refInputBox = tc
            tc.MoveBeforeInTabOrder(self.inputBox)
            sizerG1.Add(tc, (1, 0), flag=c)

            st = wx.StaticText(self, -1, "Ref Phase (deg)")
            sizerG1.Add(st, (0, 5), (1, 1), chb, 0)
            tc = wx.TextCtrl(self, -1, "%7g" % Sdeg, size=tsz)
            self.refPhaseBox = tc
            sizerG1.Add(tc, (1, 5), flag=c)
            self.sizerV.Fit(self)
            Sdeg = 0.
        else:
            Sdb += float(self.refInputBox.GetValue())

        # append values to calibration table text
        self.editBox.AppendText("\n%d %7g %7g" % (adc, Sdb, Sdeg))
        self.OnCleanUp(0)


#==============================================================================
# The PDM Calibration dialog box.

class PDMCalDialog(wx.Dialog):
    def __init__(self, frame):
        self.frame = frame
        p = frame.prefs
        if msa.IsScanning():
            msa.StopScan()
        pos = p.get("pdmCalWinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1, "PDM Calibration", pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        c = wx.ALIGN_CENTER
        sizerV = wx.BoxSizer(wx.VERTICAL)
        st = wx.StaticText(self, -1, \
        "The actual phase shift caused by PDM inversion will differ from "\
        "the theoretical 180 degrees. A one-time calibration is required to "\
        "determine the actual phase shift. This value will be used "\
        "internally, and you will not directly need to know or use the "\
        "value. To perform this calibration you first need to do the "\
        "following, which will require that you close this window and return "\
        "to the Graph Window:\n\n"\
        "    * Set Video Filter to NARROW bandwidth.\n"\
        "    * Connect Tracking Generator output to MSA input with 1-2 foot "\
                "cable.\n"\
        "    * In menu Operating Cal->Transmission, set Transmission "\
                "Reference to No Reference.\n"\
        "    * Sweeping 0-200 MHz, find a frequency with a phase shift near "\
                "90 or 270 deg.\n"\
        "    * Center the sweep at that frequency, with zero sweep width.\n"\
        "    * Return to this window and click the PDM Inversion Cal button.")
        st.Wrap(600)
        sizerV.Add(st, 0, c|wx.ALL, 10)

        btn = wx.Button(self, -1, "Set Up")
        btn.Bind(wx.EVT_BUTTON, self.OnCalSetup)
        sizerV.Add(btn, 0, c|wx.ALL, 5)
        btn = wx.Button(self, -1, "PDM Inversion Cal")
        btn.Bind(wx.EVT_BUTTON, self.OnPDMInversionCal)
        sizerV.Add(btn, 0, c|wx.ALL, 5)
        self.invDeg = p.invDeg
        self.invBox =tb = wx.StaticText(self, -1,
                                "Current Inversion= %g deg" % self.invDeg)
        sizerV.Add(tb, 0, c)

        # Cancel and OK buttons
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        butSizer.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, wx.ID_CANCEL)
        butSizer.Add(btn, 0, wx.ALL, 5)
        btn = wx.Button(self, wx.ID_OK)
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(butSizer, 0, wx.ALIGN_RIGHT|wx.ALIGN_BOTTOM|wx.ALL, 10)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if pos == wx.DefaultPosition:
            self.Center()

    #--------------------------------------------------------------------------
    # Set up for a PDM calibration.

    def OnCalSetup(self, event):
        frame = self.frame
        p = frame.prefs
        if frame.sweepDlg:
            frame.sweepDlg.OnClose()
        p.fStart = 0.
        p.fStop = 200.
        p.nSteps = 100
        p.planeExt = 3*[0.]
        p.isLogF = False
        frame.SetCalLevel(0)
        frame.SetMode(msa.MODE_VNATran)
        frame.ScanPrecheck(True)

    #--------------------------------------------------------------------------
    # Find the amount of phase shift when the PDM state is inverted.
    # invDeg is a calibration value used in CaptureOneStep(),
    # (phase of inverted PDM) - (invDeg) = real phase of PDM.
    # The VNA must be in "0" sweepwidth, freq close to the transition point.

    def OnPDMInversionCal(self, event):
        frame = self.frame
        p = frame.prefs
        print ("Calibrating PDM Inversion")
        msa.wait = 250
        msa.invDeg = 192.
        msa.invPhase = 0
        msa.WrapStep()
        freq, adc, Sdb, phase0 = \
            msa.CaptureOneStep(post=False, useCal=False, bypassPDM=True)
        print ("phase0= %8.3f freq= %8.6f adc=%5d" % (phase0, freq, msa._phasedata))
        msa.invPhase = 1
        msa.WrapStep()
        freq, adc, Sdb, phase1 = \
            msa.CaptureOneStep(post=False, useCal=False, bypassPDM=True)
        print ("phase0= %8.3f freq= %8.6f adc=%5d" % (phase1, freq, msa._phasedata))
        msa.wait = p.wait
        self.invDeg = round(mod(phase1 - phase0, 360), 2)
        self.invBox.SetLabel("Current Inversion= %g deg" % self.invDeg)


#==============================================================================
# The Test Setups dialog box.

class TestSetupsDialog(wx.Dialog):
    def __init__(self, frame):
        self.frame = frame
        self.prefs = p = frame.prefs
        pos = p.get("testSetupsWinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1, "Test Setups", pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)

        # the subset of prefs variables that define a test setup
        self.setupVars = ("calLevel", "calThruDelay", "dataMode", "fStart",
            "fStop", "indexRBWSel", "isCentSpan", "isLogF", "continuous",
            "markerMode", "mode", "nSteps", "normRev", "planeExt", "rbw",
            "sigGenFreq", "spurTest", "sweepDir", "sweepRefresh", "tgOffset",
            "va0", "va1", "vb0", "vb1", "vFilterSelName", "wait")

        # get a list of saved-test-setup files
        self.setupsDir = directory = os.path.join(appdir, "MSA_Info", "TestSetups")
        if not os.path.exists(directory):
            os.makedirs(directory)
        # get descriptions from first line in files (minus leading '|')
        names = ["Empty"] * 16
        for fn in os.listdir(directory):
            if len(fn) > 11 and fn[:9] == "TestSetup":
                i = int(fn[9:11]) - 1
                path = os.path.join(self.setupsDir, fn)
                names[i] = open(path).readline().strip()[1:]
        self.setupNames = names

        # instructions text
        c = wx.ALIGN_CENTER
        sizerV = wx.BoxSizer(wx.VERTICAL)
        sizerV.Add(wx.StaticText(self, -1, \
        "To save a test setup consisting of the current sweep settings and "\
        "calibration data,\nselect a slot, change the name if desired, and "\
        "click Save.\nTo load a test setup, select it and click Load."), \
        0, c|wx.ALL, 10)

        # setup chooser box
        self.setupsListCtrl = lc = wx.ListCtrl(self, -1, (0, 0), (450, 250),
            wx.LC_REPORT|wx.LC_SINGLE_SEL)
        lc.InsertColumn(0, "#")
        lc.InsertColumn(1, "Name")
        lc.SetColumnWidth(0, 30)
        lc.SetColumnWidth(1, 400)

        for i, name in enumerate(names):
            lc.InsertStringItem(i, "")
            lc.SetStringItem(i, 0, gstr(i+1))
            lc.SetStringItem(i, 1, name)

        lc.Bind(wx.EVT_LIST_ITEM_SELECTED, self.OnSetupItemSel)
        lc.Bind(wx.EVT_LEFT_DCLICK,  self.OnListDClick)
        sizerV.Add(lc, 0, c|wx.ALL, 5)

        sizerH1 = wx.BoxSizer(wx.HORIZONTAL)
        sizerH1.Add(wx.StaticText(self, -1, "Name:"), 0, c)
        self.nameBox = tc = wx.TextCtrl(self, -1, "", size=(300, -1))
        sizerH1.Add(tc, 0, c|wx.ALL, 5)
        btn = wx.Button(self, -1, "Create Name")
        btn.Bind(wx.EVT_BUTTON, self.CreateName)
        sizerH1.Add(btn, 0, c)
        sizerV.Add(sizerH1, 0, c)

        # Cancel and OK buttons
        sizerH2 = wx.BoxSizer(wx.HORIZONTAL)
        sizerH2.Add((0, 0), 0, wx.EXPAND)
        self.saveBtn = btn = wx.Button(self, -1, "Save")
        btn.Bind(wx.EVT_BUTTON, self.OnSave)
        btn.Enable(False)
        sizerH2.Add(btn, 0, wx.ALL, 5)
        self.loadBtn = btn = wx.Button(self, -1, "Load")
        btn.Bind(wx.EVT_BUTTON, self.OnLoad)
        btn.Enable(False)
        sizerH2.Add(btn, 0, wx.ALL, 5)
        self.loadWithCalBtn = btn = wx.Button(self, -1, "Load with Cal")
        btn.Bind(wx.EVT_BUTTON, self.OnLoadWithCal)
        btn.Enable(False)
        sizerH2.Add(btn, 0, wx.ALL, 5)
        self.deleteBtn = btn = wx.Button(self, -1, "Delete")
        btn.Bind(wx.EVT_BUTTON, self.OnDelete)
        btn.Enable(False)
        sizerH2.Add(btn, 0, wx.ALL, 5)
        sizerH2.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, wx.ID_OK)
        sizerH2.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(sizerH2, 0, wx.ALIGN_RIGHT|wx.ALIGN_BOTTOM|wx.ALL, 10)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if pos == wx.DefaultPosition:
            self.Center()

    #--------------------------------------------------------------------------
    # Create-Name button was pressed, or we need a new name. Build it out of
    # a shorthand for the current scan mode.

    def CreateName(self, event=None):
        p = self.prefs
        name = "%s/%s/%g to %g/Path %d" % \
            (msa.shortModeNames[p.mode], ("Linear", "Log")[p.isLogF],
            p.fStart, p.fStop, p.indexRBWSel+1)
        self.nameBox.SetValue(name)

    #--------------------------------------------------------------------------
    # A double-click in the list loads that setup file.

    def OnListDClick(self, event):
        self.OnLoadWithCal(event)
        self.Close()

    #--------------------------------------------------------------------------
    # An item in list selected- change name and button enables.

    def OnSetupItemSel(self, event):
        self.setupSel = i = event.m_itemIndex
        self.saveBtn.Enable(True)
        notEmpty = self.setupNames[i] != "Empty"
        self.loadBtn.Enable(notEmpty)
        self.loadWithCalBtn.Enable(notEmpty)
        self.deleteBtn.Enable(notEmpty)
        if notEmpty:
            self.nameBox.SetValue(self.setupNames[i])
        else:
            self.CreateName()

    #--------------------------------------------------------------------------
    # Return a TestSetup file name for the current slot.

    def SetupFileName(self):
        i = self.setupSel
        return os.path.join(self.setupsDir,"TestSetup%02d.txt" % (i+1))

    #--------------------------------------------------------------------------
    # Save pressed- write setup vars to a file as a list of
    # 'variable=value' lines.

    def OnSave(self, event):
        frame = self.frame
        i = self.setupSel
        setup = Prefs()
        p = self.prefs
        for attr in self.setupVars:
            if hasattr(p, attr):
                setattr(setup, attr, getattr(p, attr))
        name = self.nameBox.GetValue()
        self.setupNames[i] = name
        setup.save(self.SetupFileName(), header=name)
        ident = "%02d.s1p" % (self.setupSel+1)
        frame.SaveCal(msa.bandCal, frame.bandCalFileName[:-4] + ident)
        frame.SaveCal(msa.baseCal, frame.baseCalFileName[:-4] + ident)
        self.setupsListCtrl.SetStringItem(i, 1, name)
        self.loadBtn.Enable(True)
        self.loadWithCalBtn.Enable(True)
        self.deleteBtn.Enable(True)

    #--------------------------------------------------------------------------
    # Load pressed- read TestSetup file and update prefs from it.

    def OnLoad(self, event):
        frame = self.frame
        p = self.prefs
        setup = Prefs.FromFile(self.SetupFileName())
        for attr in self.setupVars:
            if hasattr(setup, attr):
                setattr(p, attr, getattr(setup, attr))
        frame.SetCalLevel(p.calLevel)
        self.CreateName()
        frame.RefreshAllParms()

    #--------------------------------------------------------------------------
    # Load with Cal pressed- additionaly load calibration files.

    def OnLoadWithCal(self, event):
        frame = self.frame
        ident = "%02d.s1p" % (self.setupSel+1)
        msa.bandCal = frame.LoadCal(frame.bandCalFileName[:-4] + ident)
        msa.baseCal = frame.LoadCal(frame.baseCalFileName[:-4] + ident)
        self.OnLoad(event)

    #--------------------------------------------------------------------------
    # Delete presed- delete the slot's TestSetup file and mark slot empty.

    def OnDelete(self, event):
        i = self.setupSel
        os.unlink(self.SetupFileName())
        self.setupNames[i] = name = "Empty"
        self.setupsListCtrl.SetStringItem(i, 1, name)
        self.CreateName()
        self.loadBtn.Enable(False)
        self.loadWithCalBtn.Enable(False)
        self.deleteBtn.Enable(False)


#==============================================================================
# A text window for results, savable to a file.

class TextWindow(wx.Frame):
    def __init__(self, frame, title, pos):
        self.frame = frame
        self.title = title
        wx.Frame.__init__(self, frame, -1, title, pos)
        scroll = wx.ScrolledWindow(self, -1)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.textBox = tc = wx.TextCtrl(scroll, -1, "",
                        style = wx.TE_MULTILINE|wx.HSCROLL)
        tc.SetFont(wx.Font(fontSize, wx.TELETYPE, wx.NORMAL, wx.NORMAL))
        sizer.Add(tc, 1, wx.EXPAND)
        scroll.SetSizer(sizer)
        scroll.Fit()
        scroll.SetScrollbars(20, 20, 100, 100)
        self.SetSize((600, 200))

        self.saveAsID = ident = wx.NewId()
        if isMac:
            # Mac uses existing menu but adds "Save As"
            self.Bind(wx.EVT_ACTIVATE, self.OnActivate)
        else:
            # create local menu bar for Windows and Linux
            mb = wx.MenuBar()
            menu = wx.Menu()
            menu.Append(ident, "Save As...")
            self.Connect(ident, -1, wx.wxEVT_COMMAND_MENU_SELECTED, self.SaveAs)
            mb.Append(menu, "&File")
            menu = wx.Menu()
            mb.Append(menu, "&Edit")
            self.SetMenuBar(mb)
        self.Bind(wx.EVT_CLOSE, self.OnExit)
        self.dirty = False

    #--------------------------------------------------------------------------
    # Write text to text window, appending to end.

    def Write(self, text):
        self.textBox.AppendText(text)
        self.dirty = True

    #--------------------------------------------------------------------------
    # Text box activated/deactivated: update related menus.

    def OnActivate(self, event):
        active = event.GetActive()
        frame = self.frame
        ident = self.saveAsID   # JGH 2/10/14
        if active:
            frame.fileMenu.Append(ident, "Save As...")  # JGH 2/10/14
            frame.Connect(ident, -1, wx.wxEVT_COMMAND_MENU_SELECTED, self.SaveAs)   # JGH 2/10/14
        else:
            frame.fileMenu.Remove(ident)    # JGH 2/10/14
        event.Skip()

    #--------------------------------------------------------------------------
    # Text box closed: optionally save any changed text to a file.

    def OnExit(self, event):
        if self.dirty:
            dlg = wx.MessageDialog(self, \
                "Do you want to save this to a file?", \
                "Save", style=wx.YES_NO|wx.CANCEL|wx.CENTER)
            answer = dlg.ShowModal()
            if answer == wx.ID_YES:
                self.SaveAs()
        event.Skip()

    #--------------------------------------------------------------------------
    # Save text to a file.

    def SaveAs(self, event=None):
        p = self.frame.prefs
        wildcard = "Text (*.txt)|*.txt"
        while True:
            dataDir = p.get("dataDir", appdir)
            dlg = wx.FileDialog(self, "Save file as...", defaultDir=dataDir,
                    defaultFile=self.title + ".txt", wildcard=wildcard,
                                style=wx.SAVE)
            answer = dlg.ShowModal()
            if answer != wx.ID_OK:
                break
            path = dlg.GetPath()
            p.dataDir = os.path.dirname(path)
            if ShouldntOverwrite(path, self.frame):
                continue
            f = open(path, "w")
            f.write(self.textBox.GetValue())
            f.close()
            print ("Wrote to", path)
            self.dirty = False
            break


#==============================================================================
# The Sweep Parameters modeless dialog window.

class SweepDialog(wx.Dialog):
    def __init__(self, frame):
        self.frame = frame
        self.mode = None
        self.modeCtrls = []   # JGH modeCtrls does not exist anywhere
        self.prefs = p = frame.prefs
        pos = p.get("sweepWinPos", (20, 720))
        wx.Dialog.__init__(self, frame, -1, "Sweep Parameters", pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        c = wx.ALIGN_CENTER

        self.sizerH = sizerH = wx.BoxSizer(wx.HORIZONTAL)
        sizerV1 = wx.BoxSizer(wx.VERTICAL)

        # Mode selection
        sizerV1.Add(wx.StaticText(self, -1, "Data Mode"), 0)
        samples = ["0(Normal Operation)", "1(Graph Mag Cal)",
                   "2(Graph Freq Cal)", "3(Graph Noisy Sine)",
                   "4(Graph 1MHz Peak)"]
        cm = wx.ComboBox(self, -1, samples[0], (0, 0), (160, -1), samples)
        self.dataModeCM = cm
        cm.Enable(False)
        sizerV1.Add(cm, 0, 0)

        # Create list of Final RBW Filters
        self.finFiltSamples = samples = []
        i = 1
        for freq, bw in msa.RBWFilters:
##            samples.append("P%d-%s-%s" % (i, gstr(freq), gstr(bw))) # JGH 1/30/14
            samples.append("P%d  %sKHz BW" % (i, gstr(bw))) # JGH 2/16/14
            i += 1

        sizerV1.Add(wx.StaticText(self, -1, "Final RBW Filter Path:"), 0)
        ##s = p.indexRBWSel  # JGH added Oct24
        ##cm = wx.ComboBox(self, -1, samples[s], (0, 0), (160, -1), samples) # JGH changed to samples[s] from samples[0]
        cm = wx.ComboBox(self, -1, samples[0], (0, 0), (160, -1), samples)
        self.RBWPathCM = cm
        sizerV1.Add(cm, 0, 0)

        # Video Filters
        sizerV1.Add(wx.StaticText(self, -1, "Video Filter / BW"), 0)
##        samples = ["Wide", "Medium", "Narrow", "XNarrow"]  # JGH added XNarrow
        samples = msa.vFilterNames
        cm = wx.ComboBox(self, -1, samples[2], (0, 0), (120, -1), samples)
        cm.Enable(True)
        cm.SetSelection(p.vFilterSelIndex)
        self.Bind(wx.EVT_COMBOBOX, self.AdjAutoWait, cm)
        self.videoFiltCM = cm
        sizerV1.Add(cm, 0, 0)

        sizerV1.Add(wx.StaticText(self, -1, "Graph Appearance"), 0)
        samples = ["Dark", "Light"]
        cm = wx.ComboBox(self, -1, samples[0], (0, 0), (120, -1), samples)
        self.graphAppearCM = cm
        sizerV1.Add(cm, 0, 0)
        sizerH.Add(sizerV1, 0, wx.ALL, 10)

        sizerV2 = wx.BoxSizer(wx.VERTICAL)
        if 0:
            # these aren't implemented yet
            self.refreshCB = chk = wx.CheckBox(self, -1, "Refresh Screen Each Scan")
            chk.Enable(False)
            sizerV2.Add(chk, 0, 0)

            self.dispSweepTimeCB = chk = wx.CheckBox(self, -1, "Display Sweep Time")
            chk.Enable(False)
            sizerV2.Add(chk, 0, 0)

            self.spurTestCB = chk = wx.CheckBox(self, -1, "Spur Test")
            chk.Enable(False)
            sizerV2.Add(chk, 0, wx.BOTTOM, 10)

        ##self.atten5CB = cb = wx.CheckBox(self, -1, "Attenuate 5dB")
        ##sizerV2.Add(cb, 0, wx.BOTTOM, 10)

        st = wx.StaticText(self, -1, "Step Attenuator")
        sizerV2.Add(st, 0, c|wx.TOP, 4)

        sizerH2 = wx.BoxSizer(wx.HORIZONTAL)
        sizerH2.Add(wx.StaticText(self, -1, "  "), 0, c|wx.RIGHT, 2)
        tc2 = wx.TextCtrl(self, -1, str(p.stepAttenDB), size=(40, -1))
        self.stepAttenBox = tc2
        sizerH2.Add(tc2, 0, 0)
        sizerH2.Add(wx.StaticText(self, -1, "dB"), 0, c|wx.LEFT, 2)
        sizerV2.Add(sizerH2, 0, wx.ALIGN_CENTER_HORIZONTAL|wx.ALL, 2)

        # Mode-dependent section: filled in by UpdateFromPrefs()
        self.modeBoxTitle = wx.StaticBox(self, -1, "")
        self.sizerVM = wx.StaticBoxSizer(self.modeBoxTitle, wx.VERTICAL)
        sizerV2.Add(self.sizerVM, 0, 0)
        sizerH.Add(sizerV2, 0, wx.ALL, 10)

        # Cent-Span or Start-Stop frequency entry
        sizerV3 = wx.BoxSizer(wx.VERTICAL)
        freqBoxTitle = wx.StaticBox(self, -1, "")
        freqBox = wx.StaticBoxSizer(freqBoxTitle, wx.HORIZONTAL)
        freqSizer = wx.GridBagSizer(0, 0)
        self.centSpanRB = rb = wx.RadioButton(self, -1, "", style= wx.RB_GROUP)
        self.Bind(wx.EVT_RADIOBUTTON, self.AdjFreqTextBoxes, rb)
        freqSizer.Add(rb, (0, 0), (2, 1), 0, 0)
        cl = wx.ALIGN_CENTER|wx.LEFT
        cr = wx.ALIGN_CENTER|wx.RIGHT
        freqSizer.Add(wx.StaticText(self, -1, "Cent"), (0, 1), (1, 1), 0, cr,2)
        self.centBox = tc = wx.TextCtrl(self, -1, "", size=(80, -1))
        freqSizer.Add(tc, (0, 2), (1, 1), 0, 0)
        self.Bind(wx.EVT_TEXT, self.AdjFreqTextBoxes, tc)
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        freqSizer.Add(wx.StaticText(self, -1, "MHz"), (0, 3), (1, 1), 0, cl,2)
        freqSizer.Add(wx.StaticText(self, -1, "Span"), (1, 1), (1, 1), cr, 2)
        self.spanBox = tc = wx.TextCtrl(self, -1, "", size=(80, -1))
        freqSizer.Add(tc, (1, 2), (1, 1), 0, 0)
        self.Bind(wx.EVT_TEXT, self.AdjFreqTextBoxes, tc)
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        freqSizer.Add(wx.StaticText(self, -1, "MHz"), (1, 3), (1, 1), cl, 2)
        self.startstopRB = rb = wx.RadioButton(self, -1, "")
        self.Bind(wx.EVT_RADIOBUTTON, self.AdjFreqTextBoxes, rb)
        freqSizer.Add(rb, (0, 4), (2, 1), wx.LEFT, 5)
        freqSizer.Add(wx.StaticText(self, -1, "Start"), (0, 5), (1, 1), 0,cr,2)
        self.startBox = tc = wx.TextCtrl(self, -1, "", size=(80, -1))
        freqSizer.Add(tc, (0, 6), (1, 1), 0, 0)
        self.Bind(wx.EVT_TEXT, self.AdjFreqTextBoxes, tc)
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        freqSizer.Add(wx.StaticText(self, -1, "MHz"), (0, 7), (1, 1), 0, cl, 2)
        freqSizer.Add(wx.StaticText(self, -1, "Stop"), (1, 5), (1, 1), 0, cr,2)
        self.stopBox = tc = wx.TextCtrl(self, -1, "", size=(80, -1))
        freqSizer.Add(tc, (1, 6), (1, 1), 0, 0)
        self.Bind(wx.EVT_TEXT, self.AdjFreqTextBoxes, tc)
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        freqSizer.Add(wx.StaticText(self, -1, "MHz"), (1, 7), (1, 1), 0, cl, 2)
        freqBox.Add(freqSizer, 0, wx.ALL, 2)
        sizerV3.Add(freqBox, 0, wx.EXPAND)

        # other sweep parameters
        sizerH3 = wx.BoxSizer(wx.HORIZONTAL)
        self.sizerH3V1 = wx.BoxSizer(wx.VERTICAL)
        self.sizerH3V1.Add(wx.StaticText(self, -1, "Steps/Sweep"), 0, wx.TOP, 5)
        sizerH3V1H1 = wx.BoxSizer(wx.HORIZONTAL)
        tc = wx.TextCtrl(self, -1, str(p.nSteps), size=(50, -1))
        self.stepsBox = tc
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        sizerH3V1H1.Add(tc, 0, c)
        self.continCB = chk = wx.CheckBox(self, -1, "Continuous")
        sizerH3V1H1.Add(chk, 0, c|wx.LEFT, 10)
        self.sizerH3V1.Add(sizerH3V1H1, 0, 0)
        self.sizerH3V1.Add(wx.StaticText(self, -1, "Wait (ms)"), 0, wx.TOP, 5)
        sizerH3V1H2 = wx.BoxSizer(wx.HORIZONTAL)
        self.waitBox = tc = wx.TextCtrl(self, -1, str(p.wait), size=(50, -1))
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        sizerH3V1H2.Add(tc, 0, c)
        self.autoWaitCB = chk = wx.CheckBox(self, -1, "Auto Wait")
        chk.Enable(True)
        chk.Bind(wx.EVT_CHECKBOX, self.configAutoWait)
        sizerH3V1H2.Add(chk, 0, c|wx.LEFT, 10)
        self.sizerH3V1.Add(sizerH3V1H2, 0, 0)
        sizerH3.Add(self.sizerH3V1, 0, 0)

        self.sizerH3V2 = wx.BoxSizer(wx.VERTICAL) # JGH 11/25/2013
        sweepBoxTitle = wx.StaticBox(self, -1, "Sweep")
        sweepSizer = wx.StaticBoxSizer(sweepBoxTitle, wx.VERTICAL)
        sweepH1Sizer = wx.BoxSizer(wx.HORIZONTAL)
        rb = wx.RadioButton(self, -1, "Linear", style= wx.RB_GROUP)
        self.linearRB = rb
        self.Bind(wx.EVT_RADIOBUTTON, self.AdjFreqTextBoxes, rb)
        sweepH1Sizer.Add(rb, 0, wx.RIGHT, 10)
        self.logRB = rb = wx.RadioButton(self, -1, "Log")
        self.Bind(wx.EVT_RADIOBUTTON, self.AdjFreqTextBoxes, rb)
        sweepH1Sizer.Add(rb, 0, 0)
        sweepSizer.Add(sweepH1Sizer, 0, 0)
        sweepH2Sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.lrRB = rb = wx.RadioButton(self, -1, "L-R", style= wx.RB_GROUP)
        sweepH2Sizer.Add(rb, 0, wx.RIGHT, 10)
        self.rlRB = rb = wx.RadioButton(self, -1, "R-L")
        sweepH2Sizer.Add(rb, 0, wx.RIGHT, 10)
        self.alternateRB = rb = wx.RadioButton(self, -1, "Alternate")
        sweepH2Sizer.Add(rb, 0, 0)
        sweepSizer.Add(sweepH2Sizer, 0, 0)
##        sizerH3.Add(sweepSizer, 0, wx.LEFT|wx.TOP, 10)
        self.sizerH3V2.Add(sweepSizer, 0, wx.LEFT|wx.TOP, 10) # JGH 11/25/2013
        sizerH3.Add(self.sizerH3V2, 0, 0) # JGH 11/25/2013
        sizerV3.Add(sizerH3, 0, 0)

        # Apply, Cancel, and OK buttons
        sizerV3.Add((0, 0), 1, wx.EXPAND)
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        butSizer.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, -1, "Apply")
        btn.Bind(wx.EVT_BUTTON, self.Apply)
        butSizer.Add(btn, 0, wx.ALL, 5)
        btn = wx.Button(self, -1, "One Scan")
        btn.Bind(wx.EVT_BUTTON, self.DoOneScan)
        btn.SetDefault()
        butSizer.Add(btn, 0, wx.ALL, 5)
        btn = wx.Button(self, wx.ID_CANCEL)
        btn.Bind(wx.EVT_BUTTON, self.OnClose)
        butSizer.Add(btn, 0, wx.ALL, 5)
        btn = wx.Button(self, wx.ID_OK)
        btn.Bind(wx.EVT_BUTTON, self.OnOK)
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV3.Add(butSizer, 0, wx.ALIGN_RIGHT)
        sizerH.Add(sizerV3, 0, wx.EXPAND|wx.ALL, 10)

        # set up Close shortcut
        if isMac:
            frame.Connect(wx.ID_CLOSE, -1, wx.wxEVT_COMMAND_MENU_SELECTED,
                self.OnClose)
            frame.closeMenuItem.Enable(True)
        else:
            # TODO: Needs clarification of its purpose
            accTbl = wx.AcceleratorTable([(wx.ACCEL_CTRL, ord('W'),
                                        wx.ID_CLOSE)])
            self.SetAcceleratorTable(accTbl)
            self.Connect(wx.ID_CLOSE, -1, wx.wxEVT_COMMAND_MENU_SELECTED,
                         self.OnClose)

        # get current parameters from prefs
        self.SetSizer(sizerH)
        if debug:
            print (">>>>6610<<<< SweepDialog goes to UpdateFromPrefs")
        self.UpdateFromPrefs()
        (self.startBox, self.centBox)[p.isCentSpan].SetFocus()
        if debug:
            print (">>>>6614<<<< SweepDialog complete")

    #--------------------------------------------------------------------------
    # Update all controls to current prefs.

    def UpdateFromPrefs(self):
        p = self.prefs
        LogGUIEvent("UpdateFromPrefs start=%g stop=%g" % (p.fStart, p.fStop))
        if p.fStop < p.fStart:
            p.fStop = p.fStart

##        self.dataModeCM.SetValue(p.get("dataMode", "0(Normal Operation)"))

        self.RBWPathCM.SetValue(self.finFiltSamples[p.indexRBWSel])
        # JGH: Get RBW switch bits (Correspond directly to msa.indexRBWSel)
        self.switchRBW = p.indexRBWSel

        # JGH: Get Video switch bits  (= vFilterSelIndex)
        self.vFilterSelName = p.vFilterSelName = self.videoFiltCM.GetValue()
        self.vFilterSelIndex = p.vFilterSelIndex = msa.vFilterNames.index(p.vFilterSelName)

        self.graphAppearCM.SetValue(p.get("graphAppear", "Light"))

        if 0:
            # these aren't implemented yet
            self.refreshCB.SetValue(p.get("sweepRefresh", True))
            self.dispSweepTimeCB.SetValue(p.get("dispSweepTime", False))
            self.spurTestCB.SetValue(p.get("spurTest", False))
        ##self.atten5CB.SetValue(p.get("atten5", False))
        self.stepAttenBox.SetValue(str(p.stepAttenDB))

        # Mode-dependent section
        oldMode = self.mode
        newMode = msa.mode
        if oldMode != newMode:
            # delete previous mode-dependent controls, if any
            sizerVM = self.sizerVM
            sizerVM.Clear(deleteWindows=True)

            # create new mode-dependent controls
            c = wx.ALIGN_CENTER
            ch = wx.ALIGN_CENTER_HORIZONTAL
            if newMode == msa.MODE_SA:
                # Spectrum Analyzer mode
                self.modeBoxTitle.SetLabel("Signal Generator")
                sizerVM.Add(wx.StaticText(self, -1, "Sig Gen Freq"), ch, 0)
                sizerH = wx.BoxSizer(wx.HORIZONTAL)
                tc = wx.TextCtrl(self, -1, str(p.sigGenFreq), size=(80, -1))
                self.sigGenFreqBox = tc
                sizerH.Add(tc, 0, 0)
                sizerH.Add(wx.StaticText(self, -1, "MHz"), 0, c|wx.LEFT, 2)
                sizerVM.Add(sizerH, 0, 0)

            elif newMode == msa.MODE_SATG:
                # Tracking Generator mode
                self.modeBoxTitle.SetLabel("Tracking Generator")
                self.tgReversedChk = chk = wx.CheckBox(self, -1, "Reversed")
                chk.SetValue(p.get("normRev", 0))
                sizerVM.Add(chk, 0, ch|wx.BOTTOM, 4)
                sizerH = wx.BoxSizer(wx.HORIZONTAL)
                sizerH.Add(wx.StaticText(self, -1, "Offset"), 0, c|wx.RIGHT, 2)
                tc = wx.TextCtrl(self, -1, str(p.tgOffset), size=(40, -1))
                self.tgOffsetBox = tc
                sizerH.Add(tc, 0, 0)
                sizerH.Add(wx.StaticText(self, -1, "MHz"), 0, c|wx.LEFT, 2)
                sizerVM.Add(sizerH, 0, 0)

            else:
                # VNA modes
                self.modeBoxTitle.SetLabel("VNA")
                st = wx.StaticText(self, -1, "PDM Inversion (deg)")
                sizerVM.Add(st, 0, c, 0)
                tc1 = wx.TextCtrl(self, -1, str(p.invDeg), size=(60, -1))
                self.invDegBox = tc1
                sizerVM.Add(tc1, 0, ch|wx.ALL, 2)
                st = wx.StaticText(self, -1, "Plane Extension")
                sizerVM.Add(st, 0, c|wx.TOP, 4)

                sizerH = wx.BoxSizer(wx.HORIZONTAL)
                sizerH.Add(wx.StaticText(self, -1, "  "), 0, c|wx.RIGHT, 2)
                tc2 = wx.TextCtrl(self, -1, str(p.planeExt[0]), size=(40, -1))
                self.planeExtBox = tc2
                sizerH.Add(tc2, 0, 0)
                sizerH.Add(wx.StaticText(self, -1, "ns"), 0, c|wx.LEFT, 2)
                sizerVM.Add(sizerH, 0, ch|wx.ALL, 2)

                # plane extensions for 2G and 3G bands are relative to 1G's
                st = wx.StaticText(self, -1, "PE Adjustments")
                sizerVM.Add(st, 0, c|wx.TOP, 4)
                sizerH = wx.BoxSizer(wx.HORIZONTAL)
                self.planeExt2G3GBox = []
                planeExt2G3G = [x - p.planeExt[0] for x in p.planeExt[1:]]
                for i, planeExt in enumerate(planeExt2G3G):
                    sizerH.Add(wx.StaticText(self, -1, " %dG:" % (i+2)), 0, \
                               c|wx.RIGHT, 2)
                    sizerH.Add((2, 0), 0, 0)
                    tc2 = wx.TextCtrl(self, -1, str(planeExt), size=(40, -1))
                    self.planeExt2G3GBox.append(tc2)
                    sizerH.Add(tc2, 0, 0)
                sizerH.Add(wx.StaticText(self, -1, "ns"), 0, c|wx.LEFT, 2)
                sizerVM.Add(sizerH, 0, ch|wx.ALL, 2)

                # For reflection only, Graph R()
                if newMode == msa.MODE_VNARefl:
                    self.sizerH3V1.Add(wx.StaticText(self, -1, "Graph R()"), 0, wx.TOP, 7)
                    sizerH3V1H3 = wx.BoxSizer(wx.HORIZONTAL)
                    p.graphR = p.get("graphR", 50)
                    self.graphRBox = tc = wx.TextCtrl(self, -1, str(p.graphR), size=(50, -1))
                    tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
                    tc.Enable(True)
                    sizerH3V1H3.Add(tc, 0, wx.ALIGN_LEFT)
                    sizerH3V1H3.Add(wx.StaticText(self, -1, "  ohms"), 0, c|wx.LEFT, 2)
                    self.sizerH3V1.Add(sizerH3V1H3, 0, wx.ALIGN_LEFT)

                # DUT Forward/Reverse
                fwdrevBoxTitle = wx.StaticBox(self, -1, "DUT Fwd/Rev")
                fwdrevSizer = wx.StaticBoxSizer(fwdrevBoxTitle, wx.VERTICAL)
                fwdrevH1Sizer = wx.BoxSizer(wx.HORIZONTAL)
                rb = wx.RadioButton(self, -1, "Forward", style= wx.RB_GROUP)
                self.forwardFR = rb
                self.Bind(wx.EVT_RADIOBUTTON, self.SetDUTfwdrev, rb)
                fwdrevH1Sizer.Add(rb, 0, wx.RIGHT, 10)
                self.reverseFR = rb = wx.RadioButton(self, -1, "Reverse")
                self.Bind(wx.EVT_RADIOBUTTON, self.SetDUTfwdrev, rb)
                fwdrevH1Sizer.Add(rb, 4, 0)
                fwdrevSizer.Add(fwdrevH1Sizer, 4, 0)
                self.sizerH3V2.Add(fwdrevSizer, 0, wx.LEFT|wx.TOP|wx.EXPAND, 12)

            self.mode = newMode
            sizerVM.Layout()

        # Cent-Span or Start-Stop frequency entry
        isCentSpan = p.get("isCentSpan", True)
        self.centSpanRB.SetValue(isCentSpan)
        fCent, fSpan = StartStopToCentSpan(p.fStart, p.fStop, p.isLogF)
        self.centBox.SetValue(mhzStr(fCent))
        self.spanBox.SetValue(mhzStr(fSpan))
        self.startstopRB.SetValue(not isCentSpan)
        self.startBox.SetValue(str(p.fStart))
        self.stopBox.SetValue(str(p.fStop))

        # other sweep parameters
        self.stepsBox.SetValue(str(p.nSteps))
        self.continCB.SetValue(p.get("continuous", False))
        #self.waitBox.SetValue(str(p.wait))
        if self.autoWaitCB.GetValue() == False: # JGH 12/18/13
            self.waitBox.SetValue(str(p.get("wait", 10)))
        else:
            self.calculateWait
            self.waitBox.SetValue(str(p.wait))

        isLogF = p.get("isLogF", False)
        self.linearRB.SetValue(not isLogF)
        self.logRB.SetValue(isLogF)
        sweepDir = p.get("sweepDir", 0)
        self.lrRB.SetValue(sweepDir == 0)
        self.rlRB.SetValue(sweepDir == 1)
        self.alternateRB.SetValue(sweepDir == 2)

        self.AdjFreqTextBoxes(final=True)
        self.sizerH.Fit(self)

        #--------------------------------------------------------------------------

    def configAutoWait(self, event):  # JGH added this method
        sender = event.GetEventObject()
        p = self.frame.prefs
        if sender.GetValue() == True:
            # Set the wait time to 10 x time constant of video filter
            # With R=10K and C in uF, RC = C/100 secs and wait=C/10 secs = 100C msecs
            self.calculateWait()
        else:
            # Set value = Leave in Wait box
            p.wait = int(self.waitBox.GetValue())

        #--------------------------------------------------------------------------

    def calculateWait(self):    # JGH added this  method
        p = self.frame.prefs
        p.vFilterSelName = self.videoFiltCM.GetValue()
        p.vFilterSelIndex = msa.vFilterNames.index(p.vFilterSelName)
        p.wait = int(10 + 67 *(float(p.vFilterCaps[p.vFilterSelIndex][p.vFilterSelName][0])) ** 0.32)
        self.waitBox.SetValue(str(p.wait))

        #--------------------------------------------------------------------------

    def AdjAutoWait(self, name):
        if self.autoWaitCB.GetValue() == True:
            self.calculateWait()
        else:
            pass

        #--------------------------------------------------------------------------

    def SetDUTfwdrev(self, event):  # JGH added this method
        sender = event.GetEventObject()
        p = self.frame.prefs
        p.DUTfwdrev = sender.GetValue()
        if sender.GetValue() == 0:  # Forward
            p.switchFR = 0
        else:
            p.switchFR = 1  # Reverse
 
    #--------------------------------------------------------------------------
    # One Scan pressed- apply before scanning.

    def DoOneScan(self, event):
        self.Apply()
        self.frame.DoExactlyOneScan()

    #--------------------------------------------------------------------------
    # Only enable selected freq text-entry boxes, and make other values track.

    def AdjFreqTextBoxes(self, event=None, final=False):
        isLogF = self.logRB.GetValue()
        isCentSpan = self.centSpanRB.GetValue()
        self.centBox.Enable(isCentSpan)
        self.spanBox.Enable(isCentSpan)
        self.startBox.Enable(not isCentSpan)
        self.stopBox.Enable(not isCentSpan)

        if isCentSpan:
            fCent = floatOrEmpty(self.centBox.GetValue())
            fSpan = floatOrEmpty(self.spanBox.GetValue())
            if final and fSpan < 0 and self.tcWithFocus != self.stopBox:
                fSpan = 0
                self.spanBox.ChangeValue(mhzStr(fSpan))
            fStart, fStop = CentSpanToStartStop(fCent, fSpan, isLogF)
            self.startBox.ChangeValue(mhzStr(fStart))
            self.stopBox.ChangeValue(mhzStr(fStop))
        else:
            fStart = floatOrEmpty(self.startBox.GetValue())
            fStop = floatOrEmpty(self.stopBox.GetValue())
            if final and fStop < fStart:
                if self.tcWithFocus == self.startBox:
                    fStop = fStart
                    self.stopBox.ChangeValue(mhzStr(fStop))
                else:
                    fStart = fStop
                    self.startBox.ChangeValue(mhzStr(fStart))
            fCent, fSpan = StartStopToCentSpan(fStart, fStop, isLogF)
            self.centBox.ChangeValue(mhzStr(fCent))
            self.spanBox.ChangeValue(mhzStr(fSpan))

        if isLogF and final:
            fStart = max(fStart, 0.001)
            fStop = max(fStop, 0.001)
            self.startBox.ChangeValue(mhzStr(fStart))
            self.stopBox.ChangeValue(mhzStr(fStop))
            fCent, fSpan = StartStopToCentSpan(fStart, fStop, isLogF)
            self.centBox.ChangeValue(mhzStr(fCent))
            self.spanBox.ChangeValue(mhzStr(fSpan))

    #--------------------------------------------------------------------------
    # Grab new values from sweep dialog box and update preferences.

    def Apply(self, event=None):
        frame = self.frame
        specP = frame.specP
        p = self.prefs
        LogGUIEvent("Apply")
##        p.dataMode = self.dataModeCM.GetValue()

        i = self.RBWPathCM.GetSelection()
        # JGH added in case the SweepDialog is opened and closed with no action
        if i >= 0:
            msa.indexRBWSel = p.indexRBWSel = i
        # JGH end
        (msa.finalfreq, msa.finalbw) = p.RBWFilters[p.indexRBWSel]
        p.rbw = msa.finalbw # JGH added
        p.switchRBW = p.indexRBWSel
##        msa.bitsRBW = self.bitsRBW = 4 * p.switchRBW
##        if debug: # JGH Same prints, different location. Will be removed
##            print (">>>6965<<< p.RBWFilters[p.indexRBWSel]: ", \
##                   p.RBWFilters[p.indexRBWSel])
##            print (">>> 6967 <<<< p.rbw: ", p.rbw)
##            print (">>>6968<<< bitsRBW: ", msa.bitsRBW)

        self.calculateWait

        i = self.videoFiltCM.GetSelection()
        if i>= 0:
            msa.vFilterSelIndex = p.vFilterSelIndex = i

        p.vFilterSelIndex = self.vFilterSelIndex
        p.vFilterSelName = self.vFilterSelName
##        msa.bitsVideo = self.bitsVideo = 1 * p.vFilterSelIndex
##        if debug:
##            print (">>>7205<<< bitsVideo: ", msa.bitsVideo)
##
##        msa.bitsBand = 64 * self.switchBand
##
##        msa.bitsFR = 16 * self.switchFR
##
##        msa.bitsTR = 32 * self.switchTR
##
##        msa.bitsPulse = 128 * self.switchPulse

        p.graphAppear = self.graphAppearCM.GetValue()
        p.theme = (DarkTheme, LightTheme)[p.graphAppear == "Light"]
        if 0:
            # these aren't implemented yet
            p.sweepRefresh = self.refreshCB.IsChecked()
            p.dispSweepTime = self.dispSweepTimeCB.IsChecked()
            p.spurTest = self.spurTestCB.IsChecked()
        ##p.atten5 = self.atten5CB.IsChecked()
        p.atten5 = False
        p.stepAttenDB = attenDB = floatOrEmpty(self.stepAttenBox.GetValue())
        frame.SetStepAttenuator(attenDB)
        if self.mode == msa.MODE_SA:
            p.sigGenFreq = floatOrEmpty(self.sigGenFreqBox.GetValue())
        elif self.mode == msa.MODE_SATG:
            p.normRev = self.tgReversedChk.GetValue()
            p.tgOffset = floatOrEmpty(self.tgOffsetBox.GetValue())
        else:
            p.invDeg = floatOrEmpty(self.invDegBox.GetValue())
            p.planeExt = [floatOrEmpty(self.planeExtBox.GetValue())]
            p.planeExt += [floatOrEmpty(box.GetValue()) + p.planeExt[0] \
                           for box in self.planeExt2G3GBox]
        p.isCentSpan = self.centSpanRB.GetValue()
        p.nSteps = int(self.stepsBox.GetValue())
        p.continuous = self.continCB.GetValue()


        if self.autoWaitCB.GetValue() == True:   # JGH 11/27/13
            self.calculateWait()
            self.waitBox.SetValue(str(p.wait))
            if debug:
                print ("waitBox: ", self.waitBox.GetValue())
        else:
            p.wait = int(self.waitBox.GetValue())
#        if p.wait > 255:
#            p.wait = 255
#            self.waitBox.SetValue(str(p.wait))
        if p.wait < 0:
            p.wait = 0
            self.waitBox.SetValue(str(p.wait))
##        p.autoWait = self.autoWaitCB.GetValue() #JGH 11/27/13 remmed out

        p.isLogF = self.logRB.GetValue()
        self.AdjFreqTextBoxes(final=True)
        p.fStart = floatOrEmpty(self.startBox.GetValue())
        p.fStop = floatOrEmpty(self.stopBox.GetValue())

        if self.lrRB.GetValue():
            p.sweepDir = 0
        elif self.rlRB.GetValue():
            p.sweepDir = 1
        else:
            p.sweepDir = 2

        frame.StopScanAndWait()
##        cb.SetP(4, msa.bitsVideo + msa.bitsRBW + msa.bitsFR +
##                msa.bitsTR + msa.bitsBand + msa.bitsPulse)

        msa.NewScanSettings(p)
        frame.spectrum = None
        specP.results = None

        LogGUIEvent("Apply: new spectrum")
        frame.ReadCalPath()
        frame.ReadCalFreq()
        specP.FullRefresh()

    #--------------------------------------------------------------------------
    # Key focus changed.

    def OnSetFocus(self, event):
        tc = event.GetEventObject()
        if isMac:
            tc.SelectAll()
        self.tcWithFocus = tc
        event.Skip()

    #--------------------------------------------------------------------------
    # Close pressed- save parameters back in prefs and close window.

    def OnClose(self, event=None):
        frame = self.frame
        frame.closeMenuItem.Enable(False)
        self.prefs.sweepWinPos = self.GetPosition().Get()
        self.Destroy()
        frame.sweepDlg = None

    def OnOK(self, event):
        self.Apply()
        self.OnClose()


#==============================================================================
# The Vertical Scale Parameters dialog window.

class VScaleDialog(wx.Dialog):
    def __init__(self, specP, vScale, pos):
        self.specP = specP
        self.vScale = vScale
        self.prefs = specP.prefs
        units = vScale.dataType.units
        wx.Dialog.__init__(self, specP, -1, "Vert %s Scale" % units,
                            pos, wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        c = wx.ALIGN_CENTER
        chb = wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_BOTTOM
        cvr = wx.ALIGN_CENTER_VERTICAL|wx.ALIGN_RIGHT

        # limits entry
        sizerV = wx.BoxSizer(wx.VERTICAL)
        sizerGB = wx.GridBagSizer(10, 8)
        st = wx.StaticText(self, -1, "Top Ref")
        sizerGB.Add(st, (0, 1), flag=cvr)
        self.topRefTC = tc = wx.TextCtrl(self, -1,
                            si(vScale.top, flags=SI_ASCII), size=(80, -1))
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        tc.Bind(wx.EVT_KILL_FOCUS, self.OnKillFocus)
        sizerGB.Add(tc, (0, 2), flag=c)
        st = wx.StaticText(self, -1, "Bot Ref")
        sizerGB.Add(st, (1, 1), flag=cvr)
        self.botRefTC = tc = wx.TextCtrl(self, -1,
                            si(vScale.bot, flags=SI_ASCII), size=(80, -1))
        tc.Bind(wx.EVT_SET_FOCUS, self.OnSetFocus)
        tc.Bind(wx.EVT_KILL_FOCUS, self.OnKillFocus)
        sizerGB.Add(tc, (1, 2), flag=c)
        btn = wx.Button(self, -1, "Auto Scale")
        btn.Bind(wx.EVT_BUTTON, self.OnAutoScale)
        sizerGB.Add(btn, (2, 2), flag=c)

        # graph data select
        st = wx.StaticText(self, -1, "Graph Data")
        sizerGB.Add(st, (0, 4), flag=chb)
        typeList = traceTypesLists[msa.mode]
        choices = [ty.desc for ty in typeList]
        i = min(vScale.typeIndex, len(choices)-1)
        cbox = wx.ComboBox(self, -1, choices[i], (0, 0), (200, -1), choices)
        cbox.SetStringSelection(choices[i])
        self.typeSelCB = cbox
        self.Bind(wx.EVT_COMBOBOX, self.OnSelectType, cbox)
        sizerGB.Add(cbox, (1, 4), flag=c)
        self.MaxHoldChk = chk = wx.CheckBox(self, -1, "Max Hold")
        chk.SetValue(vScale.maxHold)
        chk.Bind(wx.EVT_CHECKBOX ,self.OnMaxHold)
        sizerGB.Add(chk, (2, 4), flag=c)
        sizerGB.AddGrowableCol(3)

        # TODO: VScale primary trace entry
        # TODO: VScale priority entry
        sizerV.Add(sizerGB, 0, wx.EXPAND|wx.ALL, 20)

        # Cancel and OK buttons
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        butSizer.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, wx.ID_CANCEL)
        butSizer.Add(btn, 0, wx.ALL, 5)
        btn = wx.Button(self, wx.ID_OK)
        btn.SetDefault()
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(butSizer, 0, wx.ALIGN_RIGHT)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if pos == wx.DefaultPosition:
            self.Center()

    #--------------------------------------------------------------------------
    # Update vert scale parameters from dialog.

    def Update(self):
        specP = self.specP
        vScale = self.vScale
        vScale.top = floatSI(self.topRefTC.GetValue())
        vScale.bot = floatSI(self.botRefTC.GetValue())
        specP.frame.DrawTraces()
        specP.FullRefresh()

    #--------------------------------------------------------------------------
    # Key focus changed.

    def OnSetFocus(self, event):
        if isMac:
            tc = event.GetEventObject()
            tc.SelectAll()
        event.Skip()

    def OnKillFocus(self, event):
        self.Update()
        event.Skip()

    #--------------------------------------------------------------------------
    # Auto Scale pressed- calculate new top, bottom values.

    def OnAutoScale(self, event):
        specP = self.specP
        vScale = self.vScale
        vScale.AutoScale(self.specP.frame)
        self.topRefTC.SetValue(si(vScale.top, flags=SI_ASCII))
        self.botRefTC.SetValue(si(vScale.bot, flags=SI_ASCII))
        specP.frame.DrawTraces()
        specP.FullRefresh()

    def OnMaxHold(self, event):
        specP = self.specP
        vScale = self.vScale
        vScale.maxHold = hold = self.MaxHoldChk.GetValue()
        name = vScale.dataType.name
        trace = specP.traces[name]
        trace.maxHold = hold
        trace.max = False

    #--------------------------------------------------------------------------
    # A graph data type selected- if new, remember it and run auto scale.

    def OnSelectType(self, event):
        vScale = self.vScale
        i = self.typeSelCB.GetSelection()

        if i != vScale.typeIndex:
            # have chosen a new data type: perform auto-scale
            vScale.typeIndex = i
            vScale.dataType = dataType = traceTypesLists[msa.mode][i]
            vScale.top = self.top = dataType.top
            vScale.bot = self.bot = dataType.bot
            if self.top == 0 and self.bot == 0:
                self.OnAutoScale(event)
            else:
                self.topRefTC.SetValue(si(self.top, flags=SI_ASCII))
                self.botRefTC.SetValue(si(self.bot, flags=SI_ASCII))
                self.Update()
        else:
            self.Update()


#==============================================================================
# A Reference line. Created by copying another spectrum.

class Ref(Spectrum):
    def __init__(self, refNum):
        self.refNum = refNum
        self.aColor = None
        self.bColor = None
        self.aWidth = 1
        self.bWidth = 1
        self.mathMode = 0

    @classmethod
    def FromSpectrum(cls, refNum, spectrum, vScale):
        this = cls(refNum)
        this.spectrum = dcopy.deepcopy(spectrum)
        this.vScale = vScale
        ##this.aColor = vColors[refNum]
        return this


#==============================================================================
# The Reference Line dialog box.

class RefDialog(wx.Dialog):
    def __init__(self, frame, refNum):
        self.frame = frame
        self.refNum = refNum
        self.prefs = p = frame.prefs
        self.ref = ref = frame.refs.get(refNum)
        pos = p.get("refWinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1,
                            "Reference Line %d Specification" % refNum, pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        sizerV = wx.BoxSizer(wx.VERTICAL)
        c = wx.ALIGN_CENTER
        chb = wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_BOTTOM

        # instructions
        st = wx.StaticText(self, -1, \
        "You may create reference lines from fixed values, the current "\
        "data, or by simulating an RLC circuit. You may select to graph the "\
        "reference and the input data, or to graph the result of adding or "\
        "subtracting them.")
        st.Wrap(600)
        sizerV.Add(st, 0, c|wx.ALL|wx.EXPAND, 10)

        # reference label box
        sizerH1 = wx.BoxSizer(wx.HORIZONTAL)
        sizerH1.Add(wx.StaticText(self, -1, "Name:"), 0, c|wx.RIGHT, 4)
        name = "R%d" % refNum
        if ref:
            name = ref.name
        self.nameBox = tc = wx.TextCtrl(self, -1, name, size=(80, -1))
        tc.SetFocus()
        tc.SetInsertionPoint(len(name))
        sizerH1.Add(tc, 0, c)
        sizerV.Add(sizerH1, 0, c|wx.ALL, 5)

        # reference mode
        self.mode = 1
        choices = ["No Reference Lines", "Use Current Data", "Use Fixed Value"]
        if msa.mode >= msa.MODE_VNATran:
            choices += ["Use RLC Circuit"]
        self.modeRB = rb = wx.RadioBox(self, -1, choices=choices,
                        majorDimension=3, style=wx.RA_HORIZONTAL)
        rb.SetSelection(self.mode)
        self.Bind(wx.EVT_RADIOBOX, self.SetMode, rb)
        sizerV.Add(rb, 0, c|wx.ALL, 10)

        # right trace
        self.traceEns = [False, False]
        sizerG1 = wx.GridBagSizer()
        self.traceEns[0] = chk = wx.CheckBox(self, -1, "Do Trace for Right Axis")
        chk.SetValue(True)
        sizerG1.Add(chk, (0, 0), (1, 3), c|wx.BOTTOM, 5)
        sizerG1.Add(wx.StaticText(self, -1, "Color"), (1, 0), flag=chb)
        nColors = len(p.theme.vColors)
        color = p.theme.vColors[(2*refNum) % nColors].Get(False)
        cs = csel.ColourSelect(self, -1, "", color, size=(45, 25))
        self.colSelA = cs
        cs.Bind(csel.EVT_COLOURSELECT, self.OnSelectColorA)
        sizerG1.Add(cs, (2, 0), flag=c)
        sizerG1.Add(wx.StaticText(self, -1, "Width"), (1, 1), flag=chb)
        choices = [str(i) for i in range(1, 7)]
        cbox = wx.ComboBox(self, -1, "1", (0, 0), (50, -1), choices)
        self.widthACB = cbox
        if ref:
            cbox.SetValue(str(ref.aWidth))
        sizerG1.Add(cbox, (2, 1), (1, 1), c|wx.LEFT|wx.RIGHT, 10)
        sizerG1.Add(wx.StaticText(self, -1, "Value"), (1, 2), flag=chb)
        self.valueABox = tc = wx.TextCtrl(self, -1, "", size=(80, -1))
        tc.Enable(False)
        sizerG1.Add(tc, (2, 2), flag=c)
        sizerG1.Add((1, 10), (3, 0))

        if msa.mode >= msa.MODE_VNATran:
            # left trace
            chk = wx.CheckBox(self, -1, "Do Trace for Left Axis")
            self.traceEns[1] = chk
            chk.SetValue(True)
            sizerG1.Add(chk, (4, 0), (1, 3), c|wx.BOTTOM|wx.TOP, 5)
            sizerG1.Add(wx.StaticText(self, -1, "Color"), (5, 0), flag=chb)
            color = p.theme.vColors[(2*refNum+1) % nColors].Get(False)
            cs = csel.ColourSelect(self, -1, "", color, size=(45, 25))
            self.colSelB = cs
            cs.Bind(csel.EVT_COLOURSELECT, self.OnSelectColorB)
            sizerG1.Add(cs, (6, 0), flag=c)
            sizerG1.Add(wx.StaticText(self, -1, "Width"), (5, 1), flag=chb)
            choices = [str(i) for i in range(1, 7)]
            cbox = wx.ComboBox(self, -1, "1", (0, 0), (50, -1), choices)
            self.widthBCB = cbox
            if ref:
                cbox.SetValue(str(ref.bWidth))
            sizerG1.Add(cbox, (6, 1), (1, 1), c|wx.LEFT|wx.RIGHT, 10)
            sizerG1.Add(wx.StaticText(self, -1, "Value"), (5, 2), flag=chb)
            self.valueBBox = tc = wx.TextCtrl(self, -1, "", size=(80, -1))
            tc.Enable(False)
            sizerG1.Add(tc, (6, 2), flag=c)

        # graph options
        if refNum == 1:
            choices = ["Data and Ref", "Data + Ref", "Data - Ref",
                       "Ref - Data"]
            self.graphOptRB = rb = wx.RadioBox(self, -1, "Graph Options",
                            choices=choices, style=wx.RA_VERTICAL)
            if ref:
                rb.SetSelection(ref.mathMode)
            sizerG1.Add(rb, (0, 4), (6, 1), c)
            sizerG1.AddGrowableCol(3)
        sizerV.Add(sizerG1, 0, wx.EXPAND|wx.LEFT|wx.RIGHT, 30)

        # Cancel and OK buttons
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        butSizer.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, wx.ID_CANCEL)
        butSizer.Add(btn, 0, wx.ALL, 5)
        btn = wx.Button(self, wx.ID_OK)
        btn.SetDefault()
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(butSizer, 0, wx.ALIGN_RIGHT|wx.ALL, 10)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if pos == wx.DefaultPosition:
            self.Center()

    #--------------------------------------------------------------------------
    # Set mode: 0=No Ref Lines, 1=Current Data, 2=Fixed Value.

    def SetMode(self, event):
        self.mode = mode = event.GetInt()
        self.nameBox.Enable(mode > 0)
        self.traceEns[0].Enable(mode > 0)
        self.colSelA.Enable(mode > 0)
        self.widthACB.Enable(mode > 0)
        self.valueABox.Enable(mode > 1)
        if msa.mode >= msa.MODE_VNATran:
            self.traceEns[1].Enable(mode > 0)
            self.colSelB.Enable(mode > 0)
            self.widthBCB.Enable(mode > 0)
            self.valueBBox.Enable(mode > 1)
        if self.refNum == 1:
            self.graphOptRB.Enable(mode > 0)

    #--------------------------------------------------------------------------
    # Got a result from color chooser- change corresponding vColor preference.

    def OnSelectColorA(self, event):
        vColors = self.prefs.theme.vColors
        nColors = len(vColors)
        #ref = self.refs.get(self.refNum).iColor #JGH 2/10/14 (ref not used)
        vColors[(2*self.refNum) % nColors] = wx.Colour(*event.GetValue())

    def OnSelectColorB(self, event):
        vColors = self.prefs.theme.vColors
        nColors = len(vColors)
        vColors[(2*self.refNum+1) % nColors] = wx.Colour(*event.GetValue())

    def OnHelp(self, event):
        pass


#==============================================================================
# The Perform Calibration dialog box.

class PerformCalDialog(MainDialog):
    def __init__(self, frame):
        self.frame = frame
        p = frame.prefs
        pos = p.get("perfCalWinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1, "Perform Calibration", pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        self.sizerV = sizerV = wx.BoxSizer(wx.VERTICAL)
        c = wx.ALIGN_CENTER

        # text box, filled in by Update()
        self.textBox = wx.StaticText(self, -1, "")
        sizerV.Add(self.textBox, 0, wx.TOP|wx.LEFT|wx.RIGHT, 10)

        # optional thru delay
        self.calThruBox = None
        # DISABLED- expected to be set up beforehand in Sweep dialog
        if msa.mode == msa.MODE_VNATran: # EON Jan 10 2014
            sizerH = wx.BoxSizer(wx.HORIZONTAL)
            st = wx.StaticText(self, -1,
                    "Delay of Calibration Through Connection:")
            sizerH.Add(st, 0, c|wx.RIGHT, 8)
            delText = "%g" % p.get("calThruDelay", 0)
            tc = wx.TextCtrl(self, -1, delText, size=(40, -1))
            self.calThruBox = tc
            tc.SetInsertionPoint(len(delText))
            sizerH.Add(tc, 0, c)
            sizerH.Add(wx.StaticText(self, -1, "ns"), 0, c|wx.LEFT, 5)
            sizerV.Add(sizerH, 0, wx.LEFT, 20)

        if 0 and msa.mode == MSA.MODE_VNARefl: #EON add 0 to disable 12/24/2013
            # test fixture
            sizerH1 = wx.BoxSizer(wx.HORIZONTAL)
            sizerH1.Add((1, 1), 1, wx.EXPAND)
            series = p.get("isSeriesFix", True)
            shunt = p.get("isShuntFix", False)
            sizerH1.Add(self.FixtureBox(series, shunt), 0, wx.ALIGN_TOP)
            sizerH1.Add((1, 1), 1, wx.EXPAND)
            sizerV.Add(sizerH1, 0, c)

        #  buttons
        sizerG1 = wx.GridBagSizer(10, 10)
        self.perfBandCalBtn = btn = wx.Button(self, -1, "Perform Band Cal")
        # Start EON Jan 10 2014
        if msa.mode == msa.MODE_VNATran:
            btn.Bind(wx.EVT_BUTTON, self.OnPerformBandCal)
        else:
            btn.Bind(wx.EVT_BUTTON, self.ReflBandCalDialog)
        # End EON Jan 10 2014
        sizerG1.Add(btn, (0, 0), flag=c)
        self.saveAsBaseBtn = btn = wx.Button(self, -1, "Save As Base")
        btn.Bind(wx.EVT_BUTTON, self.OnSaveAsBase)
        sizerG1.Add(btn, (1, 0), flag=c)
        self.clearBandBtn = btn = wx.Button(self, -1, "Clear Band Cal")
        btn.Bind(wx.EVT_BUTTON, self.OnClearBandCal)
        sizerG1.Add(btn, (0, 1), flag=c)
        self.clearBaseBtn =  btn = wx.Button(self, -1, "Clear Base Cal")
        btn.Bind(wx.EVT_BUTTON, self.OnClearBaseCal)
        sizerG1.Add(btn, (1, 1), flag=c)
        self.helpBtn = btn = wx.Button(self, -1, "Help")
        btn.Bind(wx.EVT_BUTTON, self.OnHelp)
        sizerG1.Add(btn, (0, 3)) # EON Jan 22, 2014
        self.okBtn = btn = wx.Button(self, wx.ID_OK)
        btn.SetDefault()
        sizerG1.Add(btn, (1, 3)) # EON Jan 22, 2014
        sizerV.Add(sizerG1, 0, wx.EXPAND|wx.ALL, 10)
        sizerG1.AddGrowableCol(2) # EON Jan 22, 2014

        self.SetSizer(sizerV)
        self.Update()
        if pos == wx.DefaultPosition:
            self.Center()
        self.Bind(wx.EVT_CLOSE, self.OnClose)

    #--------------------------------------------------------------------------
    # Update help and info text and button enables after a change.

    def Update(self):
        frame = self.frame
        p = frame.prefs
        msg = "Connect TG output and MSA input to test fixture and attach "\
                "proper cal standards."
        if msa.mode == msa.MODE_VNATran:
            msg = "TG output must have THROUGH connection to MSA input."
        bandCalInfo = "(none)"
        spec = msa.bandCal
        # Start EON Jan 10 2014
        if spec:
            bandCalInfo = "Performed %s" % spec.desc
        baseCalInfo = "(none)"
        spec = msa.baseCal
        if spec:
            baseCalInfo = "Performed %s" % spec.desc
        # End EON Jan 10 2014
        self.textBox.SetLabel( \
        "            The MSA is currently in Path %d.\n\n"\
        "MSA calibrations are not saved separately for different paths. If "\
        "the current path is not the one for which the calibration will be "\
        "used, close this window and change the path selection. VIDEO FILTER "\
        "should be set to NARROW bandwidth for maximum smoothing. %s\n\n"\
        "Band Sweep calibration is run at the same frequency points at which "\
        "it will be used.\n\n"\
        "   Band: %s\n\n"\
        "You may save the current Band calibration as a Base calibration, to "\
        "be used as a coarse reference when the Band calibration is not "\
        "current.\n\n"\
        "   Base: %s \n\n"\
        % (p.indexRBWSel + 1, msg, bandCalInfo, baseCalInfo))
        self.textBox.Wrap(600)

        self.sizerV.Fit(self)
        frame.RefreshAllParms()

    #--------------------------------------------------------------------------
    # Perform Band Cal pressed- do a scan in calibration mode.

    def OnPerformBandCal(self, event):
        frame = self.frame
        p = frame.prefs
        if msa.IsScanning():
            msa.StopScan()
        else:
## Start EON Jan 10 2014
##            if msa.mode == MSA.MODE_VNARefl:
##                spec = frame.spectrum
##                p.isSeriesFix = self.seriesRB.GetValue()
##                p.isShuntFix = self.shuntRB.GetValue()
##                if spec:
##                    spec.isSeriesFix = p.isSeriesFix
##                    spec.isShuntFix = p.isShuntFix
## End EON Jan 10 2014
            msa.calibrating = True
            ##savePlaneExt = p.planeExt
            if self.calThruBox:
                p.calThruDelay = floatOrEmpty(self.calThruBox.GetValue())
                p.planeExt = 3*[p.calThruDelay]
            self.perfBandCalBtn.SetLabel("Cancel")
            self.EnableButtons(False)
            frame.DoExactlyOneScan()
            frame.WaitForStop()
            self.perfBandCalBtn.SetLabel("Perform Band Cal")
            self.EnableButtons(True)
            msa.calibrating = False
            ##p.planeExt = savePlaneExt
            frame.SetBandCal(dcopy.deepcopy(frame.spectrum))
            frame.SetCalLevel(2)
            self.Update()

    # Start EON Jan 10 2014
    #--------------------------------------------------------------------------
    # Reflection Band Cal

    def ReflBandCalDialog(self, event):
        p = self.frame.prefs
        from oslCal import PerformReflCalDialog
        dlg = PerformReflCalDialog(self.frame, self)
        if dlg.ShowModal() == wx.ID_OK:
            p.perfReflCalpWinPos = dlg.GetPosition().Get()
    # End EON Jan 10 2014

    #--------------------------------------------------------------------------
    # Save As Base pressed- copy band cal data to base.

    def OnSaveAsBase(self, event):
        self.frame.CopyBandToBase()
        self.Update()

    #--------------------------------------------------------------------------
    # Clear Band or Clear Base Cal pressed- clear corresponding data.

    def OnClearBandCal(self, event):
        frame = self.frame
        if msa.bandCal:
            frame.SetBandCal(None)
            if msa.baseCal:
                frame.SetCalLevel(1)
            else:
                frame.SetCalLevel(0)
            self.Update()

    def OnClearBaseCal(self, event):
        frame = self.frame
        if msa.baseCal:
            msa.baseCal = None
            # Start EON Jan 10 2014
            try:
                os.unlink(frame.baseCalFileName)
            except:
                pass
            # End EON Jan 10 2014
            if not msa.bandCal:
                frame.SetCalLevel(0)
            self.Update()

    #--------------------------------------------------------------------------
    # Help pressed- bring up help.

    def OnHelp(self, event):
        p = self.frame.prefs
        dlg = OperCalHelpDialog(self.frame)
        if dlg.ShowModal() == wx.ID_OK:
            p.operCalHelpWinPos = dlg.GetPosition().Get()

    #--------------------------------------------------------------------------
    # Disable buttons while running calibration.

    def EnableButtons(self, enable):
        self.saveAsBaseBtn.Enable(enable)
        self.clearBandBtn.Enable(enable)
        self.clearBaseBtn.Enable(enable)
        self.okBtn.Enable(enable)

    #--------------------------------------------------------------------------
    # Close- quit any running calibration.

    def OnClose(self, event):
        if msa.IsScanning():
            msa.StopScan()
            event.Skip()

#==============================================================================
# A Help dialog for Operating Cal dialog.

class OperCalHelpDialog(wx.Dialog):
    def __init__(self, frame):
        p = frame.prefs
        pos = p.get("operCalHelpWinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1, "Perform Calibration Help", pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        sizerV = wx.BoxSizer(wx.VERTICAL)
        self.SetBackgroundColour("WHITE")
        st = wx.StaticText(self, -1, "Band calibration is performed at the "\
        "frequency points of immediate interest and is used only as long as "\
        "the sweep matches those points. Base calibration is performed over "\
        "a broad frequency range, to be interpolated to the current sweep "\
        "frequencies when there is no current band calibration. To create a "\
        "Base calibration you perform a Band calibration and save it as a "\
        "Base calibration. It is intended as a convenient coarse reference, "\
        "especially when phase precision is not required. In Transmission "\
        "Mode, Base calibrations are saved in a file for use in future "\
        "sessions. In Transmision Mode you also specify the time delay of "\
        "the calibration Through connection, which is ideally zero but may "\
        "be greater if you need to use an adapter.", pos=(10, 10))
        st.Wrap(600)
        sizerV.Add(st, 0, wx.ALL, 5)

        # OK button
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        butSizer.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, wx.ID_OK)
        btn.SetDefault()
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(butSizer, 0, wx.ALIGN_RIGHT)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if pos == wx.DefaultPosition:
            self.Center()

#==============================================================================
# The Perform Calibration Update dialog box.

class PerformCalUpdDialog(wx.Dialog): # EON Jan 29, 2014
    def __init__(self, frame):
        self.frame = frame
        p = frame.prefs
        self.error = False # EON Jan 29, 2014
        if msa.calLevel != 2:
            message("Calibration Level must be Reference to Band to update.") # EON Jan 29, 2014
            self.error = True # EON Jan 29, 2014
            return
        mode = p.mode
        if mode == MSA.MODE_VNARefl:
            oslCal = msa.oslCal
            if oslCal != None:
                stdType = msa.oslCal.OSLBandRefType
            else:
                message("No OSL Calibration.") # EON Jan 29, 2014
                self.error = True # EON Jan 29, 2014
                return
        else:
            stdType = "Through connection"

        self.btnList = []
        pos = p.get("perfCalUpdWinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1, "Calibration Update", pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        self.sizerV = sizerV = wx.BoxSizer(wx.VERTICAL)
        c = wx.ALIGN_CENTER

        text = "To update the currently band calibration, attach the " + stdType +\
                " and click Perform Update. This will update the currently installed "\
                "reference to partially adjust for drift occurring since the full "\
                "calibration was performed."
        st = wx.StaticText(self, -1, text, pos=(10, 10))
        st.Wrap(500)
        sizerV.Add(st, 0, wx.ALL|wx.EXPAND, 5)

        if mode == MSA.MODE_VNATran:
            sizerH0 = wx.BoxSizer(wx.HORIZONTAL)
            text = "Delay of Calibration Through Connection (ns):"
            txt = wx.StaticText(self, -1, text)
            sizerH0.Add(txt, 0, wx.EXPAND|wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)
            delText = "%g" % p.get("calThruDelay", 0)
            self.calThruBox = tc = wx.TextCtrl(self, -1, delText, size=(40, -1))
            self.btnList.append(tc)
            sizerH0.Add(tc, 0, wx.EXPAND|wx.ALL|wx.ALIGN_CENTER_VERTICAL, 5)
            sizerV.Add(sizerH0, 0, c|wx.ALL, 10)

##        text = ("Apply update to " + # EON Jan 29, 2014
##                ("Reflection","Transmission")[mode == MSA.MODE_VNARefl] +
##                " Cal as well.")
##        self.updateBoth = chk = wx.CheckBox(self, -1, text)
##        self.btnList.append(chk)
##        sizerV.Add(chk, 0, c|wx.ALL, 10)

        sizerH1 = wx.BoxSizer(wx.HORIZONTAL)
        self.calBtn = btn = wx.Button(self, -1, "Perform " + stdType)
        btn.Bind(wx.EVT_BUTTON, self.onCal)
        sizerH1.Add(btn, 0, c|wx.ALL, 5)

        self.doneBtn = btn = wx.Button(self, -1, "Done")
        self.btnList.append(btn)
        btn.Bind(wx.EVT_BUTTON, self.onDone)
        sizerH1.Add(btn, 0, c|wx.ALL, 5)
        sizerV.Add(sizerH1, 0, c|wx.ALL, 5)

        self.SetSizer(sizerV)
        sizerV.Fit(self)

#        self.Update()
        if pos == wx.DefaultPosition:
            self.Center()
#        self.Bind(wx.EVT_CLOSE, self.OnClose)

    def EnableButtons(self, enable):
        for btn in self.btnList:
            btn.Enable(enable)

    def CalScan(self):
        frame = self.frame
        p = frame.prefs
        spectrum = frame.spectrum
        if spectrum:
            spectrum.isSeriesFix = p.isSeriesFix
            spectrum.isShuntFix = p.isShuntFix
        msa.calibrating = True
        wait = p.wait
        p.wait = calWait # EON Jan 29, 2014
        self.calBtn
        self.doneBtn
        frame.DoExactlyOneScan()
        frame.WaitForStop()
        p.wait = wait
        msa.calibrating = False
        ##p.planeExt = savePlaneExt
        return spectrum

    def onCal(self, wxEvent):
        if msa.IsScanning():
            msa.StopScan()
        else:
            self.EnableButtons(False)
            calBtn = self.calBtn
            label = calBtn.GetLabel()
            calBtn.SetLabel("Abort Cal")
            spectrum = self.CalScan()
            oslCal = msa.oslCal
            calBtn.SetLabel(label)
            self.EnableButtons(True)
            if msa.mode == MSA.MODE_VNARefl:
                for i in range (0, oslCal._nSteps):
                    Mdb = spectrum.Mdb[i]
                    Mdeg = spectrum.Mdeg[i]
                    #(db, deg) = oslCal.bandRef[i]
                    oslCal.bandRef[i] = (Mdb, Mdeg)
            else:
                cal = msa.bandCal # EON Jan 29, 2014
                if cal != None:
                    cal.Mdb = dcopy.copy(spectrum.Mdb)
                    cal.deg = dcopy.copy(spectrum.Mdeg)

    def onDone(self, wxEvent):
        self.Destroy()

#==============================================================================
# A Help dialog for a Function menu dialog.

class FunctionHelpDialog(wx.Dialog):
    def __init__(self, funcDlg):
        frame = funcDlg.frame
        p = frame.prefs
        pos = p.get(funcDlg.shortName+"HelpWinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1, funcDlg.title+" Help", pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        sizerV = wx.BoxSizer(wx.VERTICAL)
        self.SetBackgroundColour("WHITE")
        st = wx.StaticText(self, -1, funcDlg.helpText, pos=(10, 10))
        st.Wrap(600)
        sizerV.Add(st, 0, wx.ALL, 5)

        # OK button
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        butSizer.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, wx.ID_OK)
        btn.SetDefault()
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(butSizer, 0, wx.ALIGN_RIGHT)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if pos == wx.DefaultPosition:
            self.Center()

#==============================================================================
# Base class for Functions menu function dialog boxes.

class FunctionDialog(MainDialog):
    def __init__(self, frame, title, shortName):
        self.frame = frame
        self.title = title
        self.shortName = shortName
        p = frame.prefs
        self.pos = p.get(shortName+"WinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1, title, self.pos,
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        frame.StopScanAndWait()
        self.helpDlg = None
        self.R0 = p.get("R0", 50.)

    #--------------------------------------------------------------------------
    # Common button events.

    def OnHelpBtn(self, event):
        self.helpDlg = dlg = FunctionHelpDialog(self)
        dlg.Show()

    def OnClose(self, event):
        p = self.frame.prefs
        self.frame.task = None
        if msa.IsScanning():
            msa.StopScan()
            event.Skip()
        setattr(p, self.shortName+"WinPos", self.GetPosition().Get())
        p.R0 = self.R0
        helpDlg = self.helpDlg
        if helpDlg:
            setattr(p, self.shortName+"HelpWinPos", \
                                        helpDlg.GetPosition().Get())
            helpDlg.Close()
        self.Destroy()

    #--------------------------------------------------------------------------
    # Return the name of the primary "Mag" or "dB" trace.

    def MagTraceName(self):
        specP = self.frame.specP
        magNames = [x for x in specP.traces.keys() \
                if ("dB" in x) or ("Mag" in x)]
        if len(magNames) != 1:
            raise RuntimeError("No Magnitude trace found (or multiple)")
        return magNames[0]

    #--------------------------------------------------------------------------
    # Find peak frequency Fs, -3 dB (or dbDownBy) points, and Fp if fullSweep.
    # Sets self.Fs and self.Fp, and returns PeakS21DB, Fdb3A, Fdb3B.
    # If isPos is False, it finds only the notch Fp, which may be dbDownBy
    # dB up from the notch or taken as an absolute level if isAbs is True.

    def FindPoints(self, fullSweep=False, dbDownBy=3, isPos=True, isAbs=False):
        frame = self.frame
        p = frame.prefs
        specP = frame.specP
        markers = specP.markers
        specP.markersActive = True
        specP.dbDownBy = dbDownBy
        specP.isAbs = isAbs

        # place L, R, P+, P- markers on Mag trace and find peaks
        magName = self.MagTraceName()
        markers["L"]  = L =  Marker("L",  magName, p.fStart)
        markers["R"]  = R =  Marker("R",  magName, p.fStop)
        if isPos:
            markers["P+"] = Pres = Pp = Marker("P+", magName, p.fStart)
        if fullSweep or not isPos:
            markers["P-"] = Pres = Pm = Marker("P-", magName, p.fStart)
        frame.SetMarkers_PbyLR()
        wx.Yield()

        # place L and R at -3db points around P+ or P-
        (frame.SetMarkers_LRbyPm, frame.SetMarkers_LRbyPp)[isPos]()
        wx.Yield()

        # The main resonance is a peak if we have a crystal or a series RLC
        # in a series fixture, or parallel RLC in parallel fixture.
        Fres = Pres.mhz
        # For crystal we may also need to zoom in more closely around the
        # series peak to get a good read on Fs.

        if fullSweep:
            # we need to find Fp ourselves
            PeakS21DB = Pp.dbm
            self.Fs = Fs = Pp.mhz
            self.Fp = Fp = Pm.mhz
            if Fp > p.fStop - 0.00005:
                msg = "Sweep does not include enough of parallel resonance."
                raise RuntimeError(msg)
            if Fs >= Fp:
                msg = "Sweep does not show proper series resonance followed " \
                        "by parallel resonance."
                raise RuntimeError(msg)
        else:
            PeakS21DB = Pres.dbm
            self.Fres = Fres
            if isPos:
                self.Fs = Fres
            else:
                self.Fp = Fres

        if L.mhz < p.fStart or R.mhz > p.fStop:
            msg = "Sweep does not contain necessary -3 dB points."
            raise RuntimeError(msg)

        return PeakS21DB, L.mhz, R.mhz

    #--------------------------------------------------------------------------
    # Read R0 from text box.

    def GetR0FromBox(self):
        self.R0 = floatOrEmpty(self.R0Box.GetValue())
        if self.R0 < 0:
            self.R0 = 50
            alertDialog(self, "Invalid R0. 50 ohms used.", "Note")


#==============================================================================
# The Analyze Filter dialog box.

class FilterAnalDialog(FunctionDialog):
    def __init__(self, frame):
        FunctionDialog.__init__(self, frame, "Analyze Filter", "filtAn")
        # JGH 2/10/14 Next 3 lines: vars not used
##        p = frame.prefs
##        markers = frame.specP.markers
##        self.sizerV = sizerV = wx.BoxSizer(wx.VERTICAL)
        c = wx.ALIGN_CENTER
        chb = wx.ALIGN_CENTER_HORIZONTAL|wx.ALIGN_BOTTOM

        # enabler and instructions
        sizerV = wx.BoxSizer(wx.VERTICAL)
        label = "Analyze filter spectrum for bandwidth, Q and shape factor."
        self.enableCB = chk = wx.CheckBox(self, -1, label)
        chk.SetValue(True)
        sizerV.Add(chk, 0, wx.ALL, 10)
        sizerV.Add(wx.StaticText(self, -1, \
            "Ref Marker is considered the peak. X1DB (typically 3 dB)"\
            "and X2DB (perhaps 30 dB,\nor 0 dB to ignore) are the dB"\
            "levels to evaluate."), 0, c|wx.ALL, 10)

        # ref marker and DB Down selection
        sizerG = wx.GridBagSizer(hgap=20, vgap=0)
        self.mNames = mNames = ("P+", "P-")
        sizerG.Add(wx.StaticText(self, -1, "Ref Marker"), (0, 0), flag=chb)
        self.peakMarkCB = cbox = wx.ComboBox(self, -1, "P+", \
                    (0, 0), (80, -1), mNames)
        sizerG.Add(cbox, (1, 0), flag=c)
        sizerG.Add(wx.StaticText(self, -1, "X1DB Down"), (0, 1), flag=chb)
        self.x1dbBox = tc = wx.TextCtrl(self, -1, "3", size=(60, -1))
        sizerG.Add(tc, (1, 1), flag=c)
        sizerG.Add(wx.StaticText(self, -1, "X2DB Down"), (0, 2), flag=chb)
        self.x2dbBox = tc = wx.TextCtrl(self, -1, "0", size=(60, -1))
        sizerG.Add(tc, (1, 2), flag=c)
        sizerV.Add(sizerG, 0, c|wx.ALL, 10)

        # Cancel and OK buttons
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        butSizer.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, wx.ID_CANCEL)
        btn.Bind(wx.EVT_BUTTON, self.OnClose)
        butSizer.Add(btn, 0, wx.ALL, 5)
        btn = wx.Button(self, wx.ID_OK)
        btn.SetDefault()
        btn.Bind(wx.EVT_BUTTON, self.OnOK)
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(butSizer, 0, wx.ALIGN_RIGHT|wx.ALIGN_BOTTOM|wx.ALL, 10)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if self.pos == wx.DefaultPosition:
            self.Center()
        self.Show()

    #--------------------------------------------------------------------------
    # OK pressed- if enabled, analyze peak data and show in results box.

    def OnOK(self, event):
        frame = self.frame
        specP = frame.specP
        markers = specP.markers
        p = frame.prefs
        isLogF = p.isLogF

        if self.enableCB.IsChecked():
            # global prt
            # prt = True
            # enabled: set up markers for analysis
            peakName = self.peakMarkCB.GetValue()
            # print ("Analyzing filter -- peak is", peakName)
            # get the db values for the x1 and x2 analysis points and
            # force them positive
            x1db = abs(floatOrEmpty(self.x1dbBox.GetValue()))
            x2db = abs(floatOrEmpty(self.x2dbBox.GetValue()))

            # add P+/P- reference marker if necessary
            magName = self.MagTraceName()
            mPeak =  markers.get(peakName)
            if not mPeak:
                mPeak = markers[peakName] = Marker(peakName, magName, p.fStart)

            # find N-db-down points and set markers
            isPos = peakName == "P+"
            show = False
            if x2db and x2db != 3:
                PeakS21DB, Fx2dbA, Fx2dbB = self.FindPoints(False, x2db, isPos)
                if show:
                    print ("X2: PeakS21DB=", PeakS21DB, "Fx2dbA=", Fx2dbA, \
                        "Fx2dbB=", Fx2dbB)
                if x1db:
                    if x1db != 3:
                        markers["3"] = Marker("3", magName, Fx2dbA)
                        markers["4"] = Marker("4", magName, Fx2dbB)
                    else:
                        markers["1"] = Marker("1", magName, Fx2dbA)
                        markers["2"] = Marker("2", magName, Fx2dbB)
            if x1db and x1db != 3:
                PeakS21DB, Fx1dbA, Fx1dbB = self.FindPoints(False, x1db, isPos)
                markers["1"] = Marker("1", magName, Fx1dbA)
                markers["2"] = Marker("2", magName, Fx1dbB)
                if show:
                    print ("X1: PeakS21DB=", PeakS21DB, "Fx1dbA=", Fx1dbA, \
                        "Fx1dbB=", Fx1dbB)
            PeakS21DB, Fdb3A, Fdb3B = self.FindPoints(False, 3, isPos)
            if show:
                print ("3dB: PeakS21DB=", PeakS21DB, "Fdb3A=", Fdb3A, \
                    "Fdb3B=", Fdb3B)
            if x1db == 3:
                Fx1dbA, Fx1dbB = Fdb3A, Fdb3B
            if x2db == 3:
                Fx2dbA, Fx2dbB = Fdb3A, Fdb3B

            # find amount of ripple
            # This is the max extent of the data values between the peak and
            # the last minor peak before reaching the target level.
            # To find that last peak, we take the derivative of the span
            # and find the last zero crossing. Then we can use argmin, argmax
            # on the remainder of the span.
            mPeak = markers[peakName]
            trM = specP.traces[magName]
            v = trM.v
            jP = trM.Index(mPeak.mhz, isLogF)
            jEnds = []
            for i in range(2):
                mE = markers["LR"[i]]
                dirE = 2*i - 1
                jE = trM.Index(mE.mhz, isLogF)
                if abs(jP - jE) > 1:
                    de = diff(v[jE:jP:-dirE])
                    jEnds.append(jE - dirE*interp(0, -de, arange(len(de))))
                else:
                    jEnds = None
            if jEnds != None:
                span = v[jEnds[0]:jEnds[1]]
                ripple = span[span.argmax()] - span[span.argmin()]
            else:
                ripple = 0

            # compute and display filter info
            BWdb3 = Fdb3B - Fdb3A
            info = "BW(3dB)=%sHz\n" % si(BWdb3*MHz, 3)
            if x1db and x1db != 3:
                info += "BW(%gdB)=%sHz\n" % (x1db, si((Fx1dbB - Fx1dbA)*MHz,3))
            if x2db and x2db != 3:
                info += "BW(%gdB)=%sHz\n" % (x2db, si((Fx2dbB - Fx2dbA)*MHz,3))
            Q = mPeak.mhz / BWdb3
            info += "Q=%4g\n" % Q
            if x1db and x2db:
                shape = (Fx2dbB - Fx2dbA) / (Fx1dbB - Fx1dbA)
                info += "SF(%gdB)/%gdB)=%4.2f\n" % (x1db, x2db, shape)
            info += "IL=%6.3f\nRipple=%4g" % (-PeakS21DB, ripple)
            specP.results = info

        else:
            # analysis disabled: remove results box
            specP.results = None

        specP.FullRefresh()
        self.OnClose(event)

#==============================================================================
# The Component Meter dialog box.

# pointNum values: index to frequency that component is being tested at
_100kHz, _210kHz, _450kHz, _950kHz, _2MHz, _4p2MHz, _8p9MHz, _18p9MHz, \
    _40MHz = range(9)

class ComponentDialog(FunctionDialog):
    def __init__(self, frame):
        FunctionDialog.__init__(self, frame, "Component Meter", "comp")
        self.sizerV = sizerV = wx.BoxSizer(wx.VERTICAL)
        c = wx.ALIGN_CENTER
        self.helpText = \
        "Component Meter is a simple way to measure the value of components "\
        "which are known to be relatively pure resistors, capacitors or "\
        "inductors. It determines the component value from the attenuation "\
        "caused by the component in the test fixture. You select the fixture "\
        "and component type, run a single calibration, then insert and "\
        "measure components.\n\n"\
        "When you click Measure, the MSA will determine the component value "\
        "at one of several possible frequencies and display the frequency of "\
        "the measurement. The possible frequencies are those that the MSA "\
        "automatically included in the calibration. You may "\
        "increase/decrease the frequency of the measurement with the +Freq "\
        "and -Freq buttons, after pushing Stop.\n\n"\
        "The test fixture is typically an attenuator, then the component, "\
        "then another attenuator. The component may be connected in Series "\
        "between the attenuators, or may be Shunt to ground, which accounts "\
        "for the two different fixture types. The component will see a "\
        "certain resistance R0 looking at the incoming signal and the "\
        "outgoing signal. You must specify that R0, usually 50 ohms.\n\n"\
        "The Series fixture is calibrated with a Short (the terminals "\
        "directly shorted) and can typically measure R from 5 ohms to 100K "\
        "ohms; L from 10 nH to 1 mH, and C from 1 pF to 0.2 uF.\n\n"\
        "The Shunt fixture is calibrated with an Open (no component "\
        "attached) and can typically measure R from 0.25 ohms to 1 kohm; L "\
        "from 100 nH to 100 uH, and C from 20 pF to 2 uF.\n\n"\
        "For inductors, the series resistance and Q will be displayed, but "\
        "if Q>30, both Q and series resistance may be unreliable."

        # instructions
        st = wx.StaticText(self, -1, \
        "To measure resistance, capacitance or inductance you must first "\
        "calibrate. Calibrate with series shorted and shunt open. Then "\
        "insert the component and click Measure. The video filter should be "\
        "set to NARROW. Other settings will be made automatically. You can "\
        "temporarily change the frequency with +Freq or -Freq.")
        st.Wrap(600)
        sizerV.Add(st, 0, wx.ALL, 10)

        # test fixture
        sizerH1 = wx.BoxSizer(wx.HORIZONTAL)
        sizerH1.Add((1, 1), 1, wx.EXPAND)
        if msa.mode == MSA.MODE_VNATran:
            sizerH1.Add(self.FixtureBox(isSeriesFix=True, isShuntFix=True), 0, wx.ALIGN_TOP)
            sizerH1.Add((1, 1), 1, wx.EXPAND)

        # component type
        choices = ["Resistor", "Capacitor", "Inductor", "All"]
        self.typeRB = rb = wx.RadioBox(self, -1, "Component Type",
                        choices=choices, style=wx.RA_VERTICAL)
        self.Bind(wx.EVT_RADIOBOX, self.UpdateBtns, rb)
        sizerH1.Add(rb, 0, wx.ALIGN_TOP)
        sizerH1.Add((1, 1), 1, wx.EXPAND)
        sizerV.Add(sizerH1, 0, wx.EXPAND|wx.TOP, 10)
        sizerV.Add((1, 20), 0)

        # value display (StaticBox contents filled in by Update)
        self.freq = 0.1
        sizerGV = wx.GridBagSizer(hgap=2, vgap=5)
        self.freqText = st = wx.StaticText(self, -1, "")
        sizerGV.Add(st, (0, 0), flag=c)
        sizerGV.Add((20, 1), (0, 1), flag=wx.EXPAND)
        self.decFreqBtn = btn = wx.Button(self, -1, "-Freq")
        btn.Bind(wx.EVT_BUTTON, self.OnDecFreqBtn)
        sizerGV.Add(btn, (0, 2), flag=c)
        self.incFreqBtn = btn = wx.Button(self, -1, "+Freq")
        btn.Bind(wx.EVT_BUTTON, self.OnIncFreqBtn)
        sizerGV.Add(btn, (0, 3), flag=c)
        sb = wx.StaticBox(self, -1, "")
        self.sizerBV = sizerBV = wx.StaticBoxSizer(sb, wx.HORIZONTAL)
        sizerGV.Add(sizerBV, (1, 0), (1, 4), flag=c)
        sizerV.Add(sizerGV, 0, c|wx.ALL, 15)

        self.seriesRText = st = wx.StaticText(self, -1, "")
        sizerV.Add(st, 0, c, 0)

        # main buttons
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        self.debugCB = chk = wx.CheckBox(self, -1, "Debug")
        butSizer.Add(chk, 0, c)
        butSizer.Add((20, 0), 0, wx.EXPAND)
        self.calibBtn = btn = wx.Button(self, -1, "Calibrate")
        btn.Bind(wx.EVT_BUTTON, self.OnCalibrateBtn)
        butSizer.Add(btn, 0, wx.ALL, 5)
        self.measBtn = btn = wx.Button(self, -1, "Measure")
        btn.Enable(False)
        btn.Bind(wx.EVT_BUTTON, self.OnMeasureBtn)
        butSizer.Add(btn, 0, wx.ALL, 5)
        btn = wx.Button(self, -1, "Help")
        btn.Bind(wx.EVT_BUTTON, self.OnHelpBtn)
        butSizer.Add(btn, 0, wx.ALL, 5)
        self.okBtn = btn = wx.Button(self, wx.ID_OK)
        btn.SetDefault()
        btn.Bind(wx.EVT_BUTTON, self.OnClose)
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(butSizer, 0, wx.ALIGN_RIGHT|wx.ALIGN_BOTTOM|wx.ALL, 10)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if self.pos == wx.DefaultPosition:
            self.Center()
        self.calibrated = False
        self.inCal = False
        self.measuring = False
        self.oldCompType = -1
        self.pointNum = None
        self.pointNums = {}
        self.UpdateBtns()
        self.valueBoxes = {}
        self.Show()

    #--------------------------------------------------------------------------
    # Update the states of all buttons and format the value box.

    def UpdateBtns(self, event=None):
        self.freqText.SetLabel("Frequency= %sHz" % si(self.freq * MHz, 3))
        calibrated = self.calibrated
        inCal = self.inCal
        newCompType = self.typeRB.GetSelection()
        afterMeasure = calibrated and not self.measuring and \
                        self.pointNum != None and newCompType != 4
        self.calibBtn.Enable(not self.measuring)
        self.calibBtn.SetLabel(("Calibrate", "Abort")[inCal])
        self.measBtn.Enable(calibrated)
        self.decFreqBtn.Enable(afterMeasure)
        self.incFreqBtn.Enable(afterMeasure)
        self.okBtn.Enable(not inCal)

        if (newCompType == 3) != (self.oldCompType == 3) or \
                self.oldCompType < 0:
            if self.measuring:
                self.StopMeasuring()
                self.calibBtn.Enable(True)
            self.oldCompType = newCompType
            # delete any previous type-dependent controls
            sizerBV = self.sizerBV
            sizerBV.Clear(deleteWindows=True)
            # create new type's controls
            c = wx.ALIGN_CENTER
            # ch = wx.ALIGN_CENTER_HORIZONTAL
            bigFont = wx.Font(fontSize*2.0, wx.SWISS, wx.NORMAL, wx.BOLD)
            self.valueBoxes = {}
            if newCompType == 3:
                # Display all values at once
                sizerG2 = wx.GridBagSizer(5, 5)
                for j in range(2):
                    st = wx.StaticText(self, -1, ("Series", "Shunt")[j])
                    sizerG2.Add(st, (0, j), flag=c)
                    for i in range(3):
                        tc = wx.StaticText(self, -1, "----", size=(180, -1))
                        self.valueBoxes[(i,1-j)] = tc
                        tc.SetFont(bigFont)
                        tc.SetForegroundColour("BLUE")
                        sizerG2.Add(tc, (i+1,j), flag=c)
                sizerBV.Add(sizerG2, 0, 0)
            else:
                # Display selected component's value only
                tc = wx.StaticText(self, -1, "----", size=(200, -1))
                self.valueBox = tc
                tc.SetFont(bigFont)
                tc.SetForegroundColour("BLUE")
                sizerBV.Add(tc, 0, 0)
            sizerBV.Layout()
            self.sizerV.Fit(self)

    #--------------------------------------------------------------------------
    # Frequency up-down buttons for manual selection.

    def OnIncFreqBtn(self, event):
        if self.pointNum != None and self.pointNum < _40MHz:
            self.pointNum += 1
            self.Measure()

    def OnDecFreqBtn(self, event):
        if self.pointNum != None and self.pointNum > _100kHz:
            self.pointNum -= 1
            self.Measure()

    #--------------------------------------------------------------------------
    # Run calibration.

    def OnCalibrateBtn(self, event):
        frame = self.frame
        p = self.frame.prefs
        frame.task = None
        if msa.IsScanning():
            msa.StopScan()
            self.inCal = False
        else:
            # set up and run a calibration scan
            self.inCal = True
            self.UpdateBtns()
            p.isLogF = 1
            p.sweepDir = 0
            p.fStart = 0.1
            p.fStop = 40
            p.nSteps = 8
            p.wait = 10
            if msa.mode == MSA.MODE_VNARefl:
                self.calibrated = frame.PerformCal()
            else:
                msa.calibrating = True
                savePlaneExt = p.planeExt
                frame.DoExactlyOneScan()
                frame.WaitForStop()
                msa.calibrating = False
                p.planeExt = savePlaneExt
                frame.SetBandCal(dcopy.deepcopy(frame.spectrum))
                frame.SetCalLevel(2)
            self.freq = 0.1
            self.inCal = False
            self.calibrated = True
            self.measuring = False
            frame.DoExactlyOneScan()
            frame.WaitForStop()

        self.UpdateBtns()

    #--------------------------------------------------------------------------
    # Start or stop measuring.

    def OnMeasureBtn(self, event):
        frame = self.frame
        if self.measuring:
            # was measuring: stop
            self.StopMeasuring()
        else:
            # else, continue scan if needed, and grab current spectrum
            self.measBtn.SetLabel("Stop")
            msa.WrapStep()
            msa.haltAtEnd = False
            msa.ContinueScan()
            self.measuring = True
            # set up OnTimer repeating measmts while allowing other commands
            frame.task = self
        self.UpdateBtns()

    def StopMeasuring(self):
        frame = self.frame
        frame.task = None
        frame.StopScanAndWait()
        self.measBtn.SetLabel("Measure")
        self.measuring = False

    #--------------------------------------------------------------------------
    # Take a measurement, with automatic adjustment of pointNum.

    def AutoMeasure(self):
        self.pointNum = None
        for i in range(3):
            for j in range(2):
                self.pointNums[(i,j)] = None
        try:
            self.Measure()
        except:
            # don't allow repeated errors
            self.frame.task = None
            raise

    #--------------------------------------------------------------------------
    # Measure selected or all types.

    def Measure(self):
        iCompType = self.typeRB.GetSelection()
        if iCompType == 3:
            if len(self.valueBoxes) == 0:
                return
            # All: measure and display all 6 component/jig-type values at once
            for i in range(3):
                for j in range(2):
                    valueText, color, LText = self.MeasureOne(i, j)
                    tc = self.valueBoxes[(i,j)]
                    tc.SetLabel(valueText)
                    tc.SetForegroundColour(color)
                    ##if i == 2:
                    ##    self.seriesRText.SetLabel(LText)
            self.seriesRText.SetLabel("")
        else:
            # measure just the selected type
            if msa.mode == MSA.MODE_VNATran:
                isSeries = self.seriesRB.GetValue()
            else:
                isSeries = False
            valueText, color, LText = self.MeasureOne(iCompType, isSeries)
            self.valueBox.SetLabel(valueText)
            self.valueBox.SetForegroundColour(color)
            self.seriesRText.SetLabel(LText)
        self.UpdateBtns()

    #--------------------------------------------------------------------------
    # Calculate component value at point specified by pointNum, but if it is
    # None find best frequency. Get the component value (ohms, F, or H)
    # and the point number at which we measured. For L and C, we also get
    # the series resistance, which is valid if we have phase.
    # It is possible to get a negative L or C value, which means the
    # self-resonance has interfered and the measurement is not valid.

    def MeasureOne(self, iCompType, isSeries):
        self.iCompType = iCompType
        self.isSeries = isSeries
        frame = self.frame
        debugM = self.debugCB.IsChecked()
        if debugM:
            self.debugCB.SetValue(False)
        ##debugM = False
        spectrum = frame.spectrum
        Sdb = spectrum.Sdb
        if debugM:
            print ("Fmhz=", spectrum.Fmhz)
            print ("Sdb=", Sdb)

        self.compType = compType = ("R", "C", "L")[iCompType]
        compUnits = (Ohms, "F", "H")[iCompType]
        if msa.mode == MSA.MODE_VNATran:
            self.R0 = floatOrEmpty(self.R0Box.GetValue())
        else:
            self.R0 = msa.fixtureR0
        if self.typeRB.GetSelection() == 3:
            pointNum = self.pointNums[(iCompType, isSeries)]
        else:
            pointNum = self.pointNum

        # Do an initial measurement
        if pointNum == None:
            # need to find the best one -- assume we need to iterate
            nTries = 3
            if compType == "R":
                # 950kHz: high enough where LO leakages are not an issue
                pointNum = _950kHz
                nTries = 0
            else:
                lowFreqDB = Sdb[0]
                highFreqDB = Sdb[8]
                if debugM:
                    print ("lowFreqDB=", lowFreqDB, "highFreqDB=", highFreqDB, \
                        "isSeries=", isSeries, "compType=", compType)
                if compType == "C":
                    # Low impedance at 100 kHz indicates a large capacitor.
                    # High impedance at 40 MHz indicates a small capacitor.
                    # Large cap may be past its self-resonance at low freq, but
                    # will still have low impedance. Small cap will not be
                    # significantly affected by self-resonance at 40 MHz.
                    # We have to assume here small lead lengths on capacitors.
                    pointNum = _450kHz
                    if isSeries:
                        # We can tell whether we have extreme values by
                        # looking at 100 kHz and 40 MHz
                        # thresholds approx. 0.1 uF and 20 pF
                        isLowZ = lowFreqDB > -0.1
                        isHighZ = (not isLowZ) and highFreqDB < -7
                    else:
                        # thresholds approx. 0.1 uF and 100 pF
                        isLowZ = lowFreqDB < -5.5
                        isHighZ = (not isLowZ) and highFreqDB > -1.4
                    if isLowZ:
                        # Stick with lowest frequency
                        pointNum = _100kHz
                        nTries = 0
                    if isHighZ:
                        # start with highest frequency; may turn out hiZ is due
                        # to inductance
                        pointNum = _40MHz
                    if debugM:
                        print ("C: isLowZ=", isLowZ, "isHighZ=", isHighZ, \
                            "pointNum=", pointNum)
                else:
                    # Inductors are trickier, because losses can confuse the
                    # situation when just looking at S21 dB. So we make a guess
                    # at a starting point, but always do iteration, which
                    # separates L and R. Low impedance at 40 MHz indicates a
                    # very small inductor, though a lossy small inductor
                    # may be missed. It could also be large inductor that
                    # turned to a capacitor, but the iteration will take care
                    # of that case.
                    # A non-low impedance at 100 kHz indicates a large or
                    # lossy inductor. We will start with 100 kHz and iterate
                    # from there. For non-extreme inductors, we will start at
                    # 4.2 MHz and iterate
                    pointNum = _4p2MHz
                    if isSeries:
                        # thresholds 100 uH and 100 nH
                        isHighZ = lowFreqDB < -1.8
                        isLowZ = (not isHighZ) and highFreqDB > -0.45
                    else:
                        # thresholds 100 uH and 100 nH
                        isHighZ = lowFreqDB > -0.9
                        isLowZ = (not isHighZ) and highFreqDB < -3.4
                    if isHighZ:
                        # Start with lowest frequency
                        pointNum = _100kHz
                    if isLowZ:
                        # Start with highest frequency for small inductors
                        pointNum = _40MHz

#            print ("nTries ", nTries)
            for i in range(nTries):
                value, serRes = self.GetComponentValue(pointNum, debugM)
#                print (i, pointNum, value)
                # See if we are at a reasonable frequency for this comp value
                if value < 0:
                    # The component measured negative, which may be a
                    # sign it is past self-resonance, so we need to go
                    # with a low frequency; but go high if we are
                    # already low
                    if pointNum == _100kHz:
                        pointNum = _40MHz
                    else:
                        pointNum = max(_100kHz, int(pointNum/2))
#                    print ("negative selected ", pointNum)
                else:
                    if compType == "C":
#                        print ("type c ", value)
                        if isSeries:
#                            print ("series", value)
                            # series wants high Z, meaning lower freq
                            if value >= 5*nF:
                                pointNum = _100kHz
                            elif value >= 50*pF:
                                pointNum = _950kHz
                            else:
                                pointNum = _40MHz
#                            table = [(0.,     _40MHz),
#                                    ( 50*pF, _950kHz),
#                                    (  5*nF, _100kHz)]
                        else:
#                            print ("shunt", value)
                            if value >= 500*nF:
                                pointNum = _100kHz
                            elif value >= 50*nF:
                                pointNum = _210kHz
                            elif value >= 1*nF:
                                pointNum = _950kHz
                            elif value >= 100*pF:
                                pointNum = _8p9MHz
                            else:
                                pointNum = _40MHz
                            # shunt C wants low Z, meaning higher freq
#                            table = [(0.,     _40MHz),
#                                    (100*pF, _8p9MHz),
#                                    (  1*nF, _950kHz),
#                                    ( 50*nF, _210kHz),
#                                    (500*nF, _100kHz)]
                    else: # "L"
                        # Note: Inductor measurement is much less accurate
                        # without phase info, due to inductor losses. These
                        # ranges are se assuming phase is available. A prime
                        # goal is then to avoid the lowest freqs, where LO
                        # leakage has significant effect.
                        if value >= 1*mH:
                            pointNum = _100kHz
                        elif value >= 100*uH:
                            pointNum = _210kHz
                        elif value >= 10*uH:
                            pointNum = _950kHz
                        elif value >= 300*nH:
                            pointNum = _8p9MHz
                        else:
                            pointNum = _40MHz
#                        table = [(0.,     _40MHz),
#                                (300*nH, _8p9MHz),
#                                ( 10*uH, _950kHz),
#                                (100*uH, _210kHz),
#                                (  1*mH, _100kHz)]

                    # look up value in table of ranges
#                    i = bisect_right(table, (value,)) - 1
#                    if debugM:
#                        print ("value=", value, "i=", i, "table=", table)
#                    pointNum = table[i][1]
                    if debugM:
                        print ("pointNum=", pointNum)

        # get final value and series resistance
        value, serRes = self.GetComponentValue(pointNum, debugM)
#        print ("final ", pointNum, value)

        # display value, in red if out of range
        if value < 0:
            valueText = "------"
            valueColor = "RED"
        else:
            if value < (0.001, 10*fF, 500*pH)[iCompType]:
                value = 0.
            valueText = si(value, 4) + compUnits
            valueColor = "BLUE"
        R0ratio = self.R0 / 50
        if isSeries:
            lowLimit = (      -2,   1*pF,  10*nH)[iCompType]
            hiLimit  = (100*kOhm, 200*nF,   1*mH)[iCompType]
        else:
            lowLimit = (100*mOhm,  20*pF, 100*nH)[iCompType]
            hiLimit  = (  1*kOhm,   2*uF, 100*uH)[iCompType]
        if value < lowLimit*R0ratio or value > hiLimit*R0ratio:
            valueColor = "RED"

        # display frequency at which we measured
        self.freq = spectrum.Fmhz[pointNum]

        # display series resistance and Q for inductors
        LText = ""
        if compType == "L":
            if value < 0:
                Qtext = "300+"
            else:
                serX = 2*pi*self.freq*MHz * value
                Q = serX / serRes
                if Q > 300:
                    Qtext = "300+"
                else:
                    Qtext = "%g" % Q
            LText = "Series R=%5.2f Q=%s" % (serRes, Qtext)

        self.pointNums[(iCompType, isSeries)] = self.pointNum = pointNum
        return valueText, valueColor, LText

    #--------------------------------------------------------------------------
    # Calculate value of specified component.
    #
    # self.R0 of the test fixture; ignored for reflection mode, where
    #       ReflectArray data already accounts for it.
    # self.isSeries is 1 if fixture is Series; 0 if fixture is Shunt; ignored
    #     for reflection mode, where ReflectArray data already accounts for it.
    # self.compType is "R", "L" or "C"
    # step is the step at which we are measuring.
    # Returns value (Ohms, F or H) and, for L and C, series res in serRes.

    def GetComponentValue(self, step, debugM=False):
        compType = self.compType
        isSeries = self.isSeries
        R0 = self.R0
        serRes = 0.
        serX = 0.
        frame = self.frame
        spectrum = frame.spectrum

        if msa.mode == msa.MODE_VNARefl:
            specP = frame.specP
            magName = self.MagTraceName()
            trMag = specP.traces[magName]
            serRes = trMag.Zs[step].real
            if serRes < 0.001:
                serRes = 0
            elif serRes > 1e9:
                serRes = 1e9
            if compType == "R":
                serX = trMag.Zs[step].imag
                if serX > 0:
                    value = trMag.Zs[step].real
                else:
                    value = trMag.Zp[step].real
                return value, serRes
            elif compType == "C":
                value = -1 / (trMag.Zs[step].imag * trMag.w[step])
            else:
                value = trMag.Zs[step].imag / trMag.w[step]
            return min(value, 1.), serRes

        # trueFreq is frequency in Hz
        # db is S21 or S11 db of the component in the fixture
        # phase is S21 or S11 phase, unless we are in SATG mode
        trueFreq = spectrum.Fmhz[step] * MHz
        db = min(spectrum.Sdb[step], 0)
        phase = spectrum.Sdeg[step]
        if debugM:
            print ("GetCompValue: step=", step, "db=", db, "phase=", phase)

        if msa.mode == msa.MODE_SATG:
            # Calculate impedance from magnitude alone, assuming ideal phase
            # Magnitude of measured S21
            mag = 10**(db/20)
            if compType == "R":
                serX = 0
                if isSeries:
                    if mag > 0.9999:
                        serRes = 0.
                    else:
                        serRes = 2*R0 * (1 - mag) / mag
                    if debugM:
                        print ("R: mag=", mag, "serRes=", serRes)
                else:
                    if mag > 0.9999:
                        serRes = Inf
                    else:
                        serRes = R0 * mag / (2*(1 - mag))
            else:
                # L and C -- calculate reactance and then component value
                if isSeries:
                    if mag < 0.000001:
                        serX = 0.
                    else:
                        serX = 2*R0 * sqrt(1 - mag**2) / mag
                else:
                    if mag > 0.9999:
                        serX = Inf
                    else:
                        serX = R0 * mag / (2*sqrt(1 - mag**2))
                # capacitors have negative reactance
                if compType == "C":
                    serX = -serX
        else:
            # MODE_VNATran: calculate impedance from jig
            if isSeries:
                serRes, serX = SeriesJigImpedance(R0, db, phase)
            else:
                # We use no delay here, so we just provide a frequency of 1
                # assumes zero connector delay
                serRes, serX = ShuntJigImpedance(R0, db, phase, 0, 1, debugM)

        # serRes and serX now have the series resistance and reactance
        if debugM:
            print ("GetComponentValue: serRes=", serRes, "serX=", serX)
        if serRes < 0.001:
            serRes = 0.

        # if reactance is inductive, assume small resistor with parasitic
        # inductance and return series resistance
        if compType == "R":
            if serX > 0:
                return serRes, 0.
            # Here we want to return parallel resistance, because we are a
            # large resistor in parallel with parasitic capacitance
            parRes, parX = EquivParallelImped(serRes, serX)
            return parRes, 0.

        # Here for L or C. Convert reactance to component value
        if compType == "C":
            if serX == 0:
                value = 1.
            else:
                # capacitance in farads
                value = -1 / (2*pi*serX*trueFreq)
        else:
            # inductance in henries
            value = serX / (2*pi*trueFreq)
        if debugM:
            print ("--> GetComponentValue: serRes=", serRes, "value=", value, \
                    "f=", trueFreq)
        return min(value, 1.), serRes

#------------------------------------------------------------------------------
# Calculate a resistance and reactance pR and pX that when placed in
# parallel would produce an impedance of sR+j*sX.
# Returns (pR, pX).

def EquivParallelImped(sR, sX):
    print ("EquivParallelImped(R=", sR, ", sX=", sX, ")")
    if sR == Inf:
        magSquared = Inf
    else:
        magSquared = sR**2 + sX**2
    if sR == 0:
        if sX == 0:
            # target imped is zero; do small R and large X
            return 0, 1e12
        # target resistance is 0 but react is not; we need no parallel resistor
        pR = 1e12
    else:
        # res nonzero so parallel res is simple formula
        pR = magSquared / sR
    if sX == 0:
        return pR, 1e12
    return pR, magSquared / sX

#------------------------------------------------------------------------------
# Calculate impedance from S21.
# If a source and load of impedance Ro are attached to a series DUT, and
# S21 is measured, the impedance of the DUT is:
#   Z(DUT)= (2*Ro) * (1-S21)/S21 = (2*Ro) * (1/S21 - 1)
# The second formula is best when we start with S21 in db, because we can
# do the S21 inversion by taking the negative of the db value and adding
# 180 to the phase.
# special case: if S21Mag close to 0 treat the impedance as huge resistance
# Returns: Res, React

def SeriesJigImpedance(R0, db, deg):
    if db < -80:
        return Inf, Inf
    # outside range possible only through noise/rounding
    deg = min(max(deg, -90), 90)

    if db > -0.005:
        # For S21 mag near 1, the impedance is very small, and can be a
        # large capacitor, or a small resistor or inductor, or a mix.
        # In a real fixture S21 mag can even be slightly greater than
        # one, and the angle seems to be a better indicator of what we
        # have. For small reactive Z, tan(S21Deg) = -Z/(2*R0), so
        # Z = -2*R0*tan(S21Deg)
        if abs(deg) < 0.25:
            # Angle less than 0.25 degrees; assume resistance
            # Process resistance in normal way unless it is very small,
            # but make angle zero
            if db > -0.001:
                return 0., 0.
            deg = 0.
        else:
            return 0., -2*R0*tan(deg*pi/180)

    # To invert S21 while in db/angle form, negate db and angle
    lossMag = 10**(-db/20)
    lossAngle = -deg*pi/180
    # a+jb is inverted S21
    a = lossMag * cos(lossAngle)
    b = lossMag * sin(lossAngle)
    doubleR0 = 2*R0
    Res = doubleR0 * (a - 1)
    React = doubleR0 * b
    # avoids printing tiny values
    if Res < 0.001:
        Res = 0
    if abs(React) < 0.001:
        React = 0
    return Res, React

#------------------------------------------------------------------------------
# Calculate impedance from S21.
# If a source and load of impedance Ro are attached to a grounded DUT, and
# S21 is measured, the impedance of the DUT is:
#   Z(DUT)= (Ro/2) * S21 / (1-S21) = (Ro/2)/(1/S21 - 1) 'The second form works
# best for S21 originally in db
# special case: if S21Mag close to 1 treat the impedance as huge resistance

def ShuntJigImpedance(R0, db, deg, delay, freq, debugM=False):
    if debugM:
        print ("ShuntJigImpedance(R0=", R0, "db=", db, "deg=", deg, "delay=", \
            delay, "freq=", freq)
    # outside range possible only through noise/rounding
    deg = min(max(deg, -90), 90)
    extremeVal = False

    if db > -0.005:
        # For S21 mag near 1, the impedance is very large, and can be a
        # small capacitor, or a large resistor or inductor, or a mix.
        # In a real fixture S21 mag can even be slightly greater than
        # one, and the angle seems to be a better indicator of what we
        # have. For large reactive Z, tan(S21Deg) = R0/(2*Z), so
        # Z = R0/(2*tan(S21Deg))
        if abs(deg) < 0.25:
            # Angle less than 0.25 degrees; assume resistance
            # Process resistance in normal way unless it is very small,
            # but make angle zero
            if db > -0.001:
                return Inf, Inf
            deg = 0.
        else:
            React = R0/(2*tan(deg*pi/180))
            if debugM:
                print (" small dB, large ang: return 0", React)
            return 0., React

    if db < -100:
        Res = 0
        React = 0
        extremeVal = True
    if not extremeVal:
        # To invert S21 while in db/angle form, negate db and angle
        lossMag = 10**(-db/20)
        lossAngle = -deg*pi/180
        loss = lossMag * (cos(lossAngle) + 1j*sin(lossAngle))
        inv = 1 / (loss - 1)
        halfR0 = R0/2
        Res =   halfR0 * inv.real
        React = halfR0 * inv.imag
        if debugM:
            print (" not extreme: ", loss, inv, Res, React)

    # if delay != 0, then we adjust for the connector length of delay ns
    # the delay in radians is theta=2*pi*delay*freq/1e9, where delay is ns and
    # freq is Hz
    if delay != 0:
        # The impedance Res+j*React is the result of transformation by the
        # transmission line. We find the terminating impedance that produced
        # that transformed impedance. The impedance Z(DUT) which was
        # transformed into impedance Z is:
        #   Z(DUT) = 50* (Z - j*50*tan(theta)) / (50 - j*Z*tan(theta))
        # We use the same formula as is used to do the transformation, but
        # with negative length (theta).
        theta = -360 * delay * freq * ns
        Z = Res + 1j*React
        Zdut = 50 * (Z - 50j*tan(theta)) / (50 - 1j*Z*tan(theta))
        Res = Zdut.real
        React = Zdut.imag
        if debugM:
            print (" delay: ", theta, lossMag, lossAngle, Res, React)

    # avoids printing tiny values
    if Res < 0.001:
        Res = 0
    if abs(React) < 0.001:
        React = 0
    return Res, React


#==============================================================================
# The RLC Analysis dialog box.

class AnalyzeRLCDialog(FunctionDialog):
    def __init__(self, frame):
        FunctionDialog.__init__(self, frame, "RLC Analysis", "tranRLC")
        p = frame.prefs
        self.sizerV = sizerV = wx.BoxSizer(wx.VERTICAL)
        c = wx.ALIGN_CENTER

        self.helpText = \
        "RLC analysis will determine the R, L and C values for resistor, "\
        "inductor and capacitor combinations. The components may be in "\
        "series or in parallel, and either way they may be mounted in a "\
        "series or shunt fixture. The values of Q will also be determined."\
        "\n\nFor the shunt fixture, you may enter the time delay of the "\
        "connection between the actual fixture and the components; typically "\
        "on the order of 0.125 ns per inch.\n\n"\
        "You must enter the RLC Analysis function with a Transmission scan "\
        "already existing, showing the resonance peak (for series RLC in "\
        "series fixture, or parallel RLC in parallel fixture) or notch (for "\
        "series RLC in parallel fixture or parallel RLC in series fixture). "\
        "For resonance peaks, you should normally include the 3 dB points (3 "\
        "dB below a peak, or 3 dB above a dip). It is permissible, however, "\
        "to exclude one of those points. For resonant notches, you may "\
        "analyze the scan be using either the absolute -3 dB points (most "\
        "suitable for narrow notches) or the points 3 dB above the notch "\
        "bottom (most suitable for notches over 20 dB deep)."

        # description
        st = wx.StaticText(self, -1,"DETERMINATION OF COMBINED RLC PARAMETERS")
        sizerV.Add(st, 0, c|wx.TOP|wx.LEFT|wx.RIGHT, 10)
        st = wx.StaticText(self, -1, \
        "Determines individual components of an RLC combination from "\
        "resonance and 3 dB points. The scan must include the resonance and "\
        "at least one of the 3 dB points. High resolution improves "\
        "accuracy.")
        st.Wrap(600)
        sizerV.Add(st, 0, c|wx.TOP|wx.LEFT|wx.RIGHT, 10)

        # select series/parallel
        sizerG1 = wx.GridBagSizer(2, 2)
        self.parallelRLCRB = rb = wx.RadioButton(self, -1, \
            "The resistor, inductor and/or capacitor are in PARALLEL.", \
            style= wx.RB_GROUP)
        isSeriesRLC = p.get("isSeriesRLC", True)
        rb.SetValue(not isSeriesRLC)
        ##self.Bind(wx.EVT_RADIOBUTTON, self.UpdateFpBoxState, rb)
        sizerG1.Add(rb, (0, 0), (1, 6))
        self.SeriesRLCRB = rb = wx.RadioButton(self, -1, \
            "The resistor, inductor and/or capacitor are in SERIES.")
        rb.SetValue(isSeriesRLC)
        sizerG1.Add(rb, (1, 0), (1, 6))
        ##self.Bind(wx.EVT_RADIOBUTTON, self.UpdateFpBoxState, rb)
        sizerV.Add(sizerG1, 0, wx.ALL, 10)

        # test fixture
        isSeriesFix = p.get("isSeriesFix", True)
        st = self.FixtureBox(isSeriesFix, not isSeriesFix)
        sizerV.Add(st, 0, c|wx.ALL, 10)

        # select top/bottom 3dB points
        sizerG1 = wx.GridBagSizer(2, 2)
        self.useTopRB = rb = wx.RadioButton(self, -1, \
            "Use points at absolute -3 dB. (Best for narrow notches.)", \
            style= wx.RB_GROUP)
        isRLCUseTop = p.get("isRLCUseTop", True)
        rb.SetValue(not isRLCUseTop)
        self.Bind(wx.EVT_RADIOBUTTON, self.UpdateFpBoxState, rb)
        sizerG1.Add(rb, (0, 0), (1, 6))
        self.useBotRB = rb = wx.RadioButton(self, -1, \
            "Use points +3 dB from notch bottom. (Notch depth should exceed "\
            "20 dB.)")
        rb.SetValue(isRLCUseTop)
        sizerG1.Add(rb, (1, 0), (1, 6))
        self.Bind(wx.EVT_RADIOBUTTON, self.UpdateFpBoxState, rb)
        sizerV.Add(sizerG1, 0, wx.ALL, 10)

        # warning msg
        self.warnBox = st = wx.StaticText(self, -1, "")
        sizerV.Add(st, 0, c|wx.ALL, 10)
        st.SetFont(wx.Font(fontSize*1.3, wx.SWISS, wx.NORMAL, wx.BOLD))

        # text box for analysis results
        self.resultsBox = rb = wx.TextCtrl(self, -1, "", size=(400, -1))
        sizerV.Add(rb, 0, wx.EXPAND|wx.LEFT|wx.RIGHT, 20)

        #  bottom row buttons
        sizerH3 = wx.BoxSizer(wx.HORIZONTAL)
        sizerH3.Add((30, 0), 0, wx.EXPAND)
        self.analyzeBtn = btn = wx.Button(self, -1, "Analyze")
        btn.Bind(wx.EVT_BUTTON, self.OnAnalyze)
        btn.SetDefault()
        sizerH3.Add(btn, 0, c|wx.ALL, 5)
        sizerH3.Add((30, 0), 0, 0)
        self.helpBtn = btn = wx.Button(self, -1, "Help")
        btn.Bind(wx.EVT_BUTTON, self.OnHelpBtn)
        sizerH3.Add(btn, 0, c|wx.ALL, 5)
        self.okBtn = btn = wx.Button(self, wx.ID_OK)
        btn.Bind(wx.EVT_BUTTON, self.OnClose)
        sizerH3.Add(btn, 0, c|wx.ALIGN_RIGHT|wx.ALL, 5)
        sizerV.Add(sizerH3, 0, wx.ALIGN_RIGHT|wx.ALIGN_BOTTOM|wx.ALL, 10)

        self.UpdateFpBoxState()
        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if self.pos == wx.DefaultPosition:
            self.Center()
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.Show()

    #--------------------------------------------------------------------------
    # Set the warning text based on top/bottom selection.

    def UpdateFpBoxState(self, event=None):
        self.useTop = useTop = self.useTopRB.GetValue()
        self.warnBox.SetLabel( \
        "Scan must show resonant notch and at least one point " +
        ("3 dB above notch bottom.", "at absolute -3 dB level.")[useTop])

    #--------------------------------------------------------------------------
    # Analyze the scan to extract RLC parameters.
    # We determine Q from resonant frequency and -3 dB bandwidth, and directly
    # measure Rs at resonance. From Q and Rs we can calculate L and C.

    def OnAnalyze(self, event):
        frame = self.frame
        specP = frame.specP
        #markers = specP.markers
        p = frame.prefs
        #isLogF = p.isLogF
        R0 = self.R0
        p.isSeriesRLC = isSeriesRLC = self.SeriesRLCRB.GetValue()
        p.isSeriesFix = isSeriesFix = self.seriesRB.GetValue()
        # a peak is formed if components in series and fixure is series
        isPos = not (isSeriesRLC ^ isSeriesFix)
        p.useTop = useTop = self.useTopRB.GetValue()

        try:
            Rser = 0
            isAbs = not isPos and useTop
            # global prt
            # prt = True
            PeakS21DB, Fdb3A, Fdb3B = self.FindPoints(False, isPos=isPos, \
                                                      isAbs=isAbs)
            Fres = self.Fres

            if not isPos and not useTop:
                # analyze bottom of notch. Q determined from the 3 dB
                # bandwidth is assumed to be Qu
                S21 = 10**(-PeakS21DB/20)
                wp = 2*pi*Fres*MHz
                if isSeriesFix:
                    R = 2*R0 * (S21 - 1)
                else: # shunt fixture
                    R = (R0/2) / (S21 - 1)
                R = max(R, 0.001)
                BW = Fdb3B - Fdb3A
                # Qu at resonance. Accurate if notch is deep.
                Qu = Fres / BW
                if isSeriesRLC:
                    # series RLC in shunt fixture
                    Xres = R * Qu
                    QL = Xres / (R0/2)
                    Rser = R
                else:
                    # parallel RLC in shunt fixture
                    Xres = R / Qu
                    QL = Xres / (R0*2)
                    Rser = Xres / Qu

                L = Xres / wp
                C = 1 / (Xres * wp)

            else:
                # Analyze top of peak or notch
                if isSeriesRLC:
                    # For series RLC use crystal routine; Fp and Cp are bogus
                    R, C, L, Cp, Qu, QL = CrystalParameters(Fres, 1., \
                                    PeakS21DB, Fdb3A, Fdb3B, R0, isSeriesFix)
                else: # parallel RLC
                    R, C, L, Qu, QL, Rs = ParallelRLCFromScalarS21(Fres,
                                    PeakS21DB, Fdb3A, Fdb3B, R0, isSeriesFix)
                L *= uH
                C *= pF
            Fres *= MHz

        except RuntimeError:
            alertDialog(self, sys.exc_info()[1].message, "Analyze Error")
            return

        # compute and display filter info
        resText = ("F%s=%sHz, R=%s"+Ohms+", L=%sH, C=%sF, Qu=%s, QL=%s") % \
                ("ps"[isPos], si(Fres, 9), si(R, 4), si(L, 3), si(C, 3), \
                si(Qu, 3), si(QL, 3))
        if Rser > 0:
            resText += (", (Rser=%s)" % si(Rser, 3))
        self.resultsBox.SetValue(resText)

        BWdb3 = Fdb3B - Fdb3A
        info = re.sub(", ", "\n", resText) + "\n"
        info += ("BW(3dB)=%sHz" % si(BWdb3*MHz, 3))
        specP.results = info

        specP.FullRefresh()
        self.okBtn.SetDefault()

#==============================================================================
# The Crystal Analysis dialog box.

class CrystAnalDialog(FunctionDialog):
    def __init__(self, frame):
        FunctionDialog.__init__(self, frame, "Crystal Analysis", "crystal")
        p = frame.prefs
        self.sizerV = sizerV = wx.BoxSizer(wx.VERTICAL)
        c = wx.ALIGN_CENTER

        if msa.mode >= msa.MODE_VNATran:
            FsMsg = \
                "Fs is the parameter needing the most precision, and it will "\
                "be located by interpolation to find zero phase, so a step "\
                "size of 100 Hz or less likely provides sufficient accuracy."
        else:
            FsMsg = \
                "A small scan step size is important to locating Fs accurate"\
                "ly so you likely need a step size in the range 5-50 Hz."

        self.helpText = \
            "Crystal analysis will determine the motional parameters (Rm, Cm "\
            "and Lm) for a crystal. It will also determine the parallel "\
            "capacitance from lead to lead (Cp), and the series and parallel "\
            "resonant frequencies.\n\n"\
            "The crystal must be mounted in a series fixture, and you must "\
            "specify the R0 of the fixture. A regular 50-ohm fixture is "\
            "fine, but the standard for crystal analysis is 12.5 ohms.\n\n"\
            "You must enter the Crystal Analysis function with a "\
            "Transmission scan already existing, including the series "\
            "resonance peak and the -3 dB points around it. You may also "\
            "include the parallel resonance dip, or you may elect to "\
            "explicitly specify the parallel resonant frequency, which is "\
            "needed to determine Cp.\n\n"\
            "%s\n\n"\
            "You can reduce the step size by using the Zoom to Fs button, "\
            "which will rescan the area around Fs." % FsMsg

        # description
        st = wx.StaticText(self, -1, "DETERMINATION OF CRYSTAL PARAMETERS")
        sizerV.Add(st, 0, c|wx.TOP|wx.LEFT|wx.RIGHT, 10)
        st = wx.StaticText(self, -1, \
            "There must be an existing S21 scan of the crystal in a Series "\
            "Fixture. Enter the fixture R0. Select the type of scan. If "\
            "desired, click Zoom to Fs to improve the scan resolution. The "\
            "current step size is %sHz/step." % \
            si(MHz*(p.fStop - p.fStart) / p.nSteps))
        st.Wrap(600)
        sizerV.Add(st, 0, wx.ALL, 10)

        sizerG1 = wx.GridBagSizer(2, 2)
        self.fullScanRB = rb = wx.RadioButton(self, -1, \
            "The current scan extends from below the series resonance peak " \
            "to above the parallel resonance dip.", style= wx.RB_GROUP)
        rb.SetValue(True)
        self.Bind(wx.EVT_RADIOBUTTON, self.UpdateFpBoxState, rb)
        sizerG1.Add(rb, (0, 0), (1, 6))
        self.seriesScanRB = rb = wx.RadioButton(self, -1, \
            "The scan includes the series resonance peak only; the parallel" \
            " resonant frequency Fp is stated below.")
        sizerG1.Add(rb, (1, 0), (1, 6))
        self.Bind(wx.EVT_RADIOBUTTON, self.UpdateFpBoxState, rb)
        sizerV.Add(sizerG1, 0, c|wx.ALL, 10)

        # Fp entry, fixture R0, and zoom
        sizerH1 = wx.BoxSizer(wx.HORIZONTAL)
        self.FpLabel1 = st = wx.StaticText(self, -1, "Fp:")
        sizerH1.Add(st, 0, c|wx.RIGHT, 5)
        self.FpBox = tc = wx.TextCtrl(self, -1, "0", size=(80, -1))
        tc.SetInsertionPoint(2)
        sizerH1.Add(tc, 0, c)
        self.FpLabel2 = st = wx.StaticText(self, -1, "MHz")
        sizerH1.Add(st, 0, c|wx.LEFT, 5)
        sizerH1.Add((50, 0), 0, wx.EXPAND)
        self.UpdateFpBoxState()
        sizerH1.Add(wx.StaticText(self, -1, "Fixture R0:"), 0, c|wx.RIGHT, 5)
        self.R0Box = tc = wx.TextCtrl(self, -1, gstr(self.R0), size=(40, -1))
        tc.SetInsertionPoint(2)
        sizerH1.Add(tc, 0, c)
        sizerH1.Add(wx.StaticText(self, -1, Ohms), 0, c|wx.LEFT, 5)
        sizerH1.Add((50, 0), 0, wx.EXPAND)
        self.zoomToFsBtn = btn = wx.Button(self, -1, "Zoom to Fs")
        btn.Bind(wx.EVT_BUTTON, self.OnZoomToFs)
        sizerH1.Add(btn, 0, c|wx.ALL, 5)
        sizerV.Add(sizerH1, 0, c|wx.ALL, 10)

        # text box for analysis results
        self.resultsBox = rb = wx.TextCtrl(self, -1, "", size=(400, -1))
        sizerV.Add(rb, 0, wx.EXPAND|wx.LEFT|wx.RIGHT, 20)

        #  bottom row buttons
        sizerH3 = wx.BoxSizer(wx.HORIZONTAL)
        self.analyzeBtn = btn = wx.Button(self, -1, "Analyze")
        btn.Bind(wx.EVT_BUTTON, self.OnAnalyze)
        sizerH3.Add(btn, 0, c|wx.ALL, 5)
        self.rescanBtn = btn = wx.Button(self, -1, "Rescan")
        btn.Bind(wx.EVT_BUTTON, self.OnRescan)
        sizerH3.Add(btn, 0, c|wx.ALL, 5)
        sizerH3.Add((30, 0), 0, wx.EXPAND)
        self.addListBtn = btn = wx.Button(self, -1, "Add to List")
        btn.Enable(False)       # disable until we have done an analysis
        btn.Bind(wx.EVT_BUTTON, self.OnAddToList)
        sizerH3.Add(btn, 0, c|wx.ALL, 5)
        self.setIdNumBtn = btn = wx.Button(self, -1, "Set ID Num")
        btn.Bind(wx.EVT_BUTTON, self.OnSetIDNum)
        sizerH3.Add(btn, 0, c|wx.ALL, 5)
        sizerH3.Add((30, 0), 0, wx.EXPAND)
        self.helpBtn = btn = wx.Button(self, -1, "Help")
        btn.Bind(wx.EVT_BUTTON, self.OnHelpBtn)
        sizerH3.Add(btn, 0, c|wx.ALL, 5)
        self.okBtn = btn = wx.Button(self, wx.ID_OK)
        btn.Bind(wx.EVT_BUTTON, self.OnClose)
        sizerH3.Add(btn, 0, c|wx.ALIGN_RIGHT|wx.ALL, 5)
        sizerV.Add(sizerH3, 0, c|wx.ALL, 10)

        frame.ClearMarks() # extra Markers just cause visual confusion
        self.id = 1

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if self.pos == wx.DefaultPosition:
            self.Center()
        self.Bind(wx.EVT_CLOSE, self.OnClose)
        self.haveAnalyzed = False
        self.resultsWin = None
        self.Show()

    #--------------------------------------------------------------------------
    # Update the Fp setting box enable state.

    def UpdateFpBoxState(self, event=None):
        self.fullSweep = full = self.fullScanRB.GetValue()
        self.FpLabel1.Enable(not full)
        self.FpBox.Enable(not full)
        self.FpLabel2.Enable(not full)

    #--------------------------------------------------------------------------
    # Zoom frequency axis to L-R range around Fs peak. Mode set to series.

    def OnZoomToFs(self, event):
        frame = self.frame
        if msa.IsScanning():
            msa.StopScan()
        else:
            self.EnableButtons(False, self.zoomToFsBtn)
            self.rescanBtn.Enable(False)
            frame.ExpandLR()
            frame.WaitForStop()
            self.EnableButtons(True, self.zoomToFsBtn, "Zoom to Fs")
            self.fullScanRB.SetValue(False)
            self.seriesScanRB.SetValue(True)
            self.fullSweep = False

    #--------------------------------------------------------------------------
    # Analyze the scan to extract crystal parameters.

    def OnAnalyze(self, event):
        frame = self.frame
        p = frame.prefs
        specP = frame.specP
        isLogF = p.isLogF
        show = 0
        try:
            if not self.fullSweep:
                self.Fp = floatOrEmpty(self.FpBox.GetValue())

            PeakS21DB, Fdb3A, Fdb3B = self.FindPoints(self.fullSweep)
            Fs, Fp = self.Fs, self.Fp

            # Refine the value of Fs by finding the point with zero phase or
            # reactance. Reactance is best for reflection mode. The main
            # advantage of this approach is that transmission phase and
            # reflection reactance are linear near series resonance, so
            # interpolation can find Fs precisely even with a coarse scan.
            # Note: At the moment, we don't do crystal analysis in reflection
            # mode.
            if msa.mode != msa.MODE_VNATran:
                alertDialog(self, "Analysis requires VNA Trans mode", "Error")
                return

            # initially put marker 1 at Fs
            magName = self.MagTraceName()
            trMag = specP.traces[magName]
            markers = specP.markers
            markers["1"]  = m1 =  Marker("1", magName, Fs)
            m1.SetFromTrace(trMag, isLogF)
            jP = trMag.Index(m1.mhz, isLogF)

            # will be searching continuous phase for nearest
            # multiple-of-180-deg crossing
            trPh = trMag.phaseTrace
            vals = trPh.v
            targVal = floor((vals[jP]+90) / 180.) * 180.
            if show:
                print ("targVal=", targVal, "jP=", jP, "[jP]=", vals[jP])
            # now move marker 1 to where phase zero to find exact Fs
            # search to right if phase above zero at peak
            # global prt
            # prt = True
            searchR = vals[jP] > targVal
            signP = (-1, 1)[searchR]
            m1.FindValue(trPh, jP, isLogF, signP, searchR, targVal, show)
            if isnan(m1.mhz):
                m1.FindValue(trPh, jP, isLogF, -signP, 1-searchR, targVal, show)
            if 1:   # set 0 to disable zero-phase Fs use to match Basic
                Fs = m1.mhz

            specP.markersActive = True
            specP.FullRefresh()
            wx.Yield()

            self.R0 = float(self.R0Box.GetValue())

            # compute crystal parameters from measurements
            Rm, Cm, Lm, Cp, Qu, QL = \
              CrystalParameters(Fs, Fp, PeakS21DB, Fdb3A, Fdb3B, self.R0, True)
            self.Rm, self.Cm, self.Lm, self.Cp = Rm, Cm, Lm, Cp

        except RuntimeError:
            alertDialog(self, sys.exc_info()[1].message, "Analyze Error")
            return

        # show results
        self.resultsBox.SetValue("Fs=%sHz, Fp=%sHz, Rm=%s, Lm=%sH, Cm=%sF, "\
            "Cp=%sF" % (si(Fs*MHz, 9), si(Fp*MHz, 9), si(Rm, 4), si(Lm*uH),
             si(Cm*pF), si(Cp*pF)))
        self.FpBox.SetValue("%g" % Fp)
        self.addListBtn.Enable(True)
        self.haveAnalyzed = True

    #--------------------------------------------------------------------------
    # Rescan, possibly another crystal.

    def OnRescan(self, event):
        frame = self.frame
        if msa.IsScanning():
            msa.StopScan()
        else:
            self.EnableButtons(False, self.rescanBtn)
            self.zoomToFsBtn.Enable(False)
            frame.DoExactlyOneScan()
            frame.WaitForStop()
            self.EnableButtons(True, self.rescanBtn, "Rescan")

    #--------------------------------------------------------------------------
    # Copy results to "Crystal List" text window, file.

    def OnAddToList(self, event):
        p = self.frame.prefs
        rw = self.resultsWin
        if not rw:
            pos = p.get("crystalResultsWinPos", (600, 50))
            self.resultsWin = rw = TextWindow(self.frame, "CrystalList", pos)
            rw.Show()
            wx.Yield()
            self.Raise()
            rw.Write(" ID    Fs(MHz)       Fp(MHz)    Rm(ohms)   Lm(mH)" \
                     "      Cm(fF)      Cp(pF)\n")
        rw.Write("%4d %12.6f %12.6f %9.2f %11.6f %11.6f %7.2f\n" % \
            (self.id, self.Fs, self.Fp, self.Rm, self.Lm/1000, self.Cm*1000,
             self.Cp))
        self.id += 1

    #--------------------------------------------------------------------------
    # Set ID number- included in crystal results file.

    def OnSetIDNum(self, event):
        dlg = wx.TextEntryDialog(self, "Enter numeric ID for this crystal",
                "Crystal ID", "")
        if dlg.ShowModal() == wx.ID_OK:
            self.id = int(dlg.GetValue())

    #--------------------------------------------------------------------------
    # Enable/disable buttons- disabled while actively rescanning.

    def EnableButtons(self, enable, cancelBtn, label="Cancel"):
        cancelBtn.SetLabel(label)
        if enable:
            self.zoomToFsBtn.Enable(enable)
            self.rescanBtn.Enable(enable)
        self.fullScanRB.Enable(enable)
        self.seriesScanRB.Enable(enable)
        self.analyzeBtn.Enable(enable)
        self.setIdNumBtn.Enable(enable)
        if self.haveAnalyzed:
            self.addListBtn.Enable(enable)
        self.okBtn.Enable(enable)

    #--------------------------------------------------------------------------
    # Save results file upon dialog close.

    def OnClose(self, event):
        rw = self.resultsWin
        if rw:
            self.frame.prefs.crystalResultsWinPos = rw.GetPosition().Get()
            rw.Close()
        FunctionDialog.OnClose(self, event)

#------------------------------------------------------------------------------
# Calculate crystal parameters in Series or Shunt Jig.
# Can also use this for series RLC combinations; just provide a bogus Fp and
# ignore Cp.
#
# Fs: series resonance in MHz
# Fp: parallel resonance in MHz
# PeakS21DB: S21 db at Fs (a negative value in db)
# Fdb3A, Fdb3B: -3db frequencies, in MHz, around Fs;
#                   (absolute -3dB frequencies if shunt jig)
# R0: impedance of the test jig
# isSeries: True if a series jig
#
# Returns Rm, Cm(pF), Lm(uH), Cp(pF), Qu, QL

def CrystalParameters(Fs, Fp, PeakS21DB, Fdb3A, Fdb3B, R0, isSeries):
    if Fs <= 0 or Fp <= 0 or Fdb3A >= Fs or Fdb3B <= Fs:
        raise RuntimeError("Invalid frequency data for calculation:" \
                "Fs=%g Fp=%g Fdb3A=%g Fdb3B=%g" % (Fs, Fp, Fdb3A, Fdb3B))
    if R0 <= 0:
        raise RuntimeError("Invalid R0")
    S21 = 10**(-PeakS21DB/20)
    ws = 2*pi*Fs*MHz
    wp = 2*pi*Fp*MHz
    if isSeries:
        # internal crystal resistance at Fs, in ohms
        Rm = 2*R0 * (S21 - 1)
        # effective load seen by crystal--external plus internal
        Reff = 2*R0 + Rm
    else: # shunt fixture
        Rm = (R0/2) / (S21 - 1)
        Reff = R0/2 + Rm
    Rm = max(Rm, 0.001)
    BW = Fdb3B - Fdb3A
    # loaded Q at Fs
    QL = Fs / BW
    Lm = QL * Reff / ws
    Cm = 1 / (ws**2 * Lm)
    # net reactance of motional inductance and capacitance at Fp, ohms
    Xp = wp*Lm - 1/(wp*Cm)
    Cp = 1 / (wp*Xp)
    # unloaded Q is L reactance divided by series resistance
    Qu = QL * Reff / Rm
    return Rm, Cm/pF, Lm/uH, Cp/pF, Qu, QL

checkPass = True
def check(result, desired):
    global checkPass
    if abs(result - desired) > abs(result) * 0.01:
        print ("*** ERROR: expected %g, got %g" % (desired, result))
        checkPass = False
    ##else:
    ##    print ("OK. Expected %g, got %g" % (desired, result)

# To test: Use Fs = 20.015627, Fp = 20.07, PeakS21DB = -1.97,Fdb3a = 20.014599,
#           Fdb3B = 20.016655
#  (from methodology 3 in Clifton Labs "Crystal Motional Parameters")
# Results should be Rm = 6.36, Cm = 26.04 fF, Lm = 2427.9 uH, Cp = 4.79
if 0:
    Rm, Cm, Lm, Cp, Qu, QL = \
        CrystalParameters(20.015627, 20.07, -1.97, 20.014599, 20.016655, 12.5,
                            True)
    check(Rm, 6.36); check(Cm, 0.02604); check(Lm, 2427.9); check(Cp, 4.79)
    check(Qu, 47974.); check(QL, 9735.)
    if not checkPass:
        print ("*** CrystalParameters check FAILED.")

#------------------------------------------------------------------------------
# Calculate parallel RLC values in Series or Shunt Jig.
#
# Fp: parallel resonance in MHz
# PeakS21DB: S21 db at Fp (a negative value in db)
# Fdb3A, Fdb3B: -3db frequencies, in MHz, around Fs;
#                   (absolute -3dB frequencies if shunt jig)
# R0: impedance of the test jig
# isSeries: True if a series jig
#
# Returns Rp, C(pF), L(uH), Qu, QL, Rser

def ParallelRLCFromScalarS21(Fp, PeakS21DB, Fdb3A, Fdb3B, R0, isSeries):
    if Fp <= 0 or Fdb3A >= Fp or Fdb3B <= Fp:
        raise RuntimeError("Invalid frequency data for calculation:" \
                "Fp=%g Fdb3A=%g Fdb3B=%g" % (Fp, Fdb3A, Fdb3B))
    if R0 <= 0:
        raise RuntimeError("Invalid R0")
    S21 = 10**(-PeakS21DB/20)
    wp = 2*pi*Fp*MHz
    if isSeries:
        Rp = 2*R0 * (S21 - 1)
        Rsrcload = 2*R0
    else: # shunt fixture
        Rp = (R0/2) / (S21 - 1)
        Rsrcload = R0/2
    Rp = max(Rp, 0.001)
    Rnetload = Rsrcload * Rp / (Rsrcload + Rp)
    BW = Fdb3B - Fdb3A
    # loaded Q at Fp
    QL = Fp / BW
    # reactance of L, and -reactance of C, at resonance
    Xres = Rnetload / QL
    # unloaded Q is based on Rp, much larger than Rnetload
    Qu = Rp / Xres
    Rser = Xres / Qu
    L = Xres / wp
    C = 1 / (Xres * wp)
    if L < 1*pH:
        L = 0
    if C < 1*pF:
        C = 0
    return Rp, C/pF, L/uH, Qu, QL, Rser

#==============================================================================
# The Special Tests dialog box # JGH Substantial mod on 1/25/14

class DDSTests(wx.Dialog):   # EON 12/22/13

    def __init__(self, frame):
        self.frame = frame
        self.prefs = p = frame.prefs
        self.mode = None
        framePos = frame.GetPosition()
        pos = p.get("DDStestsWinPos", (framePos.x + 100, framePos.y + 100))
        wx.Dialog.__init__(self, frame, -1, "DDS Tests", pos,
                           wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        tsz = (100, -1)

        c = wx.ALIGN_CENTER
        sizerV = wx.BoxSizer(wx.VERTICAL)
        st = wx.StaticText(self, -1, \
        "The 'Set DDS1' box (#special.dds1out) is populated with the value of " \
        "the variable DDS1array(thisstep,46). The 'with DDS Clock at' box " \
        "(#special.masclkf) is populated with the value of the variable, masterclock. " \
        "The 'Set DDS3' box (#special.dds3out) is populated with the value of " \
        "the variable, DDS3array(thisstep,46).\n" \
        "The DDS clock is 'masterclock' and will always be the 64.xyzabc MHz that " \
        "was inserted in the Hardware Configuration Manager Window.\n" \
        "The DDS1 and DDS3 frequencies will depend on where the sweep was halted " \
        "before entering the DDS Tests Window. \n" \
        "All 3 boxes can be manually changed by highlighting and typing in a new value. " \
        "Clicking the Set DDS1 button will update the DDS1 box AND the masterclock. " \
        "Clicking the Set DDS3 button will update the DDS3 box AND the masterclock.\n" \
        "NOTE: The 'with DDS Clock at' box currently displays only 5 digits to " \
        "the right of the decimal. It really needs to be at least 6 for 1 Hz resolution. ")

        st.Wrap(600)
        sizerV.Add(st, 0, c|wx.ALL, 10)

        sizerGB = wx.GridBagSizer(5,5)

        # MSA Mode selection
        btn = wx.Button(self, 0, "Set DDS1")
        btn.Bind(wx.EVT_BUTTON, self.setDDS1)
        sizerGB.Add(btn,(0,0), flag=c)
        tc = wx.TextCtrl(self, 0, str(LO1.appxdds), size=tsz)
        self.dds1FreqBox = tc
        sizerGB.Add(tc,(0,1), flag=c)

        text = wx.StaticText(self, 0, " with DDS Clock at: ")
        sizerGB.Add(text,(1,0), flag=c)
        tc = wx.TextCtrl(self, 0, str(msa.masterclock), size=tsz)
        self.masterclockBox = tc;
        sizerGB.Add(tc,(1,1), flag=c)

        btn = wx.Button(self, 0, "Set DDS3")
        btn.Bind(wx.EVT_BUTTON, self.setDDS3)
        sizerGB.Add(btn,(2,0), flag=c)
        tc = wx.TextCtrl(self, 0, str(LO3.appxdds), size=tsz)
        self.dds3FreqBox = tc
        sizerGB.Add(tc,(2,1), flag=c)

        self.dds3TrackChk = chk = wx.CheckBox(self, -1, "DDS 3 Track")
        chk.SetValue(p.get("dds3Track",False))
        chk.Bind(wx.EVT_CHECKBOX, self.DDS3Track)
        sizerGB.Add(chk,(3,0), flag=c)

        self.dds1SweepChk = chk = wx.CheckBox(self, -1, "DDS 1 Sweep")
        chk.SetValue(p.get("dds1Sweep",False))
        chk.Bind(wx.EVT_CHECKBOX, self.DDS1Sweep)
        sizerGB.Add(chk,(3,1), flag=c)

        # VNA Mode selection

        if msa.mode == msa.MODE_SATG or msa.mode == msa.MODE_SA:
            pass
        else:
            btn = wx.Button(self, 0, "Change PDM")
            btn.Bind(wx.EVT_BUTTON, self.ChangePDM)
            sizerGB.Add(btn,(5,0), flag=c)

        sizerV.Add(sizerGB, 0, c|wx.ALL, 10)
        self.SetSizer(sizerV)
        sizerV.Fit(self)

        self.Bind(wx.EVT_CLOSE, self.Close)

    def DDS3Track(self, event=None):
        self.prefs.dds3Track = self.dds3TrackChk.GetValue()

    def DDS1Sweep(self, event=None):
        self.prefs.dds1Sweep = self.dds1SweepChk.GetValue()

    def ChangePDM(self, event):  #TODO
        pass

    # JGH deleted 2/1/14 (No help needed)
##    def DDSTestsHelp(self, event):


    def Close(self, event=None):
        p = self.prefs
        p.DDStestsWinPos = self.GetPosition().Get()
##        btn = wx.Button(self, -1, "CLOSE", (5, 435), (140,-1))
##        btn.Bind(wx.EVT_BUTTON, self.CloseSpecial)
##        sizerGB.Add(btn,(6,1), flag=c)
        self.Destroy()

#--------------------------------------------------------------------------
    # Set DDS to entered frequency

    def setDDS1(self, event):
        freq = float(self.dds1FreqBox.GetValue())
        print (">>>13796<<< freq: ", freq)
        self.setDDS(freq, cb.P1_DDS1DataBit, cb.P2_fqud1)

    def setDDS3(self, event):
        freq = float(self.dds3FreqBox.GetValue())
        self.setDDS(freq, cb.P1_DDS3DataBit, cb.P2_fqud3)

    def setDDS(self, freq, P1_DDSDataBit, P2_fqud):
##        ddsclock = float(self.masterclockBox.GetValue()) # JGH 2/2/14 3 lines
        print (">>>13805<<< msa.masterclock: ", msa.masterclock)
        base = int(round(divSafe(freq * (1<<32), msa.masterclock)))
        print (">>>13807<<< base: ", base)
        DDSbits = base << P1_DDSDataBit
        print (">>>13809<<< DDSbits: ", DDSbits)
        byteList = []
        P1_DDSData = 1 << P1_DDSDataBit
        print (">>>13812<<< P1_DDSData: ", P1_DDSData)
        for i in range(40):
            a = (DDSbits & P1_DDSData)
            byteList.append(a)
            DDSbits >>= 1;
        cb.SendDevBytes(byteList, cb.P1_Clk)
        cb.SetP(1, 0)
        cb.SetP(2, P2_fqud)
        cb.SetP(2, 0)
        cb.Flush()
        cb.setIdle()

#==============================================================================
# The Control Board Tests modeless dialog window.

class CtlBrdTests(wx.Dialog):
    def __init__(self, frame):
        self.frame = frame
        self.mode = None
        self.modeCtrls = []
        self.prefs = p = frame.prefs
        #framePos = frame.GetPosition() # JGH (framePos not used)
        pos = p.get("ctlBrdWinPos", wx.DefaultPosition)
        wx.Dialog.__init__(self, frame, -1, "Control Board Tests", pos,
                           wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        c = wx.ALIGN_CENTER
        lcv = wx.ALIGN_LEFT|wx.ALIGN_CENTER_VERTICAL
        ctlPins = (("17 Sel(L1)", 1), ("16 Init(L2)", 2),
                   ("14 Auto(L3)", 4), ("1 Strobe(L4)", 8))
        dataPins = (("2 D0", 0x01), ("3 D1", 0x02),
                    ("4 D2", 0x04), ("5 D3", 0x08),
                    ("6 D4", 0x10), ("7 D5", 0x20),
                    ("8 D6", 0x40), ("9 D7", 0x80))
        inputPins = (("11 Wait", 0x10), ("10 Ack", 0x08), ("12 PE", 0x04),
                     ("13 Select", 0x02), ("15 Error", 0x01))

        sizerH = wx.BoxSizer(wx.HORIZONTAL)

        sizerGB = wx.GridBagSizer(5,5)
        self.ctlByte = 0
        i = 0;
        for (label, mask) in ctlPins:
            btn = TestBtn(self, mask, False)
            sizerGB.Add(btn, (i, 0), flag=c)
            text = wx.StaticText(self, 0, "Pin " + label)
            sizerGB.Add(text, (i, 1), flag=lcv)
            i += 1

        self.dataByte = 0
        for (label, mask) in dataPins:
            btn = TestBtn(self, mask, True)
            sizerGB.Add(btn, (i, 0), flag=c)
            text = wx.StaticText(self, 0, "Pin " + label)
            sizerGB.Add(text, (i, 1), flag=lcv)
            i += 1
        sizerH.Add(sizerGB, 0, c|wx.ALL, 10)

        sizerGB = wx.GridBagSizer(5,5)
        btn = wx.Button(self, 0, "Capture Status")
        btn.Bind(wx.EVT_BUTTON, self.readStatus)
        sizerGB.Add(btn, (0,0), flag=c, span=(1,2))
        i = 1;
        self.inputData = []
        ts = wx.BORDER_SIMPLE|wx.ST_NO_AUTORESIZE|wx.ALIGN_CENTRE
        for (label, mask) in inputPins:
            text = wx.StaticText(self, 0, " ", size=(20,20), style=ts)
            self.inputData.append(text)
            sizerGB.Add(text, (i, 0), flag=c)
            text = wx.StaticText(self, 0, "Pin " + label)
            sizerGB.Add(text, (i, 1), flag=lcv)
            i += 1
        sizerH.Add(sizerGB, 0, wx.ALIGN_TOP|wx.ALL, 10)
        self.inputData.reverse()

        self.SetSizer(sizerH)
        sizerH.Fit(self)
        self.Bind(wx.EVT_CLOSE, self.Close)

    def Close(self, event=None):
        p = self.prefs
        p.ctlBrdWinPos = self.GetPosition().Get()
        self.Destroy()

    def readStatus(self,event):
        inp = 0x14
        for text in self.inputData:
            val = ("0","1")[inp & 1]
            text.SetLabel(val)
            inp >>= 1

class TestBtn(wx.Button):
    def __init__(self, parent, mask, data):
        self.mask = mask
        self.data = data
        self.parent = parent
        wx.Button.__init__(self,parent, 0, "0", size=(30,20))
        self.Bind(wx.EVT_BUTTON, self.toggleBit)

    def toggleBit(self, event):
        val = self.GetLabel()
        if val == "0":
            self.SetLabel("1")
            self.updateBit(True)
        else:
            self.SetLabel("0")
            self.updateBit(False)

    def updateBit(self,state):
        if self.data:
            if state:
                self.parent.dataByte |= self.mask
            else:
                self.parent.dataByte &= ~self.mask
            cb.OutPort(self.parent.dataByte)
        else:
            if state:
                self.parent.ctlByte |= self.mask
            else:
                self.parent.ctlByte &= ~self.mask
            cb.OutControl(self.parent.ctlByte ^ cb.contclear)
        cb.Flush()
#==============================================================================        
    

#==============================================================================
# The main MSA frame showing the spectrum.

class MSASpectrumFrame(wx.Frame):
    def __init__(self, parent, title):
        global msa, fontSize

        # read preferences file, if any
        self.refreshing = False
        self.appName = title
        self.rootName = title.lower()
        self.prefs = None
        self.LoadPrefs()
        self.consoleStderr = None

        # get preference values, using defaults if new
        p = self.prefs
        p.get("fStart", -1.5)
        p.get("fStop", 1.5)
        p.get("nSteps", 400)
        self.markMHz = 0.
        self.fHdim = fHdim = 800 ; self.fVdim = fVdim = 600 # JGH 2/16/14
        #fHdim = 800 ; fVdim = 600
        wx.Frame.__init__(self, parent, -1, title,
                            size=p.get("frameSize", (fHdim, fVdim)))
        self.Bind(wx.EVT_SIZE, self.OnSizeChanged)
        self.Bind(wx.EVT_CLOSE, self.OnExit)
        self.SetDoubleBuffered(True)

        # calibrate font point size for this system
        font = wx.Font(fontSize, wx.SWISS, wx.NORMAL, wx.NORMAL)
        font.SetPixelSize((200, 200))
        pointSize200px = font.GetPointSize()
        fontSize = fontSize * pointSize200px / 170
        if 0:
            # test it
            font10 = wx.Font(fontSize, wx.SWISS, wx.NORMAL, wx.NORMAL)
            ps10 = font10.GetPointSize()
            font27 = wx.Font(fontSize*2.7, wx.SWISS, wx.NORMAL, wx.NORMAL)
            ps27 = font27.GetPointSize()
            print ("10-point pointsize=", ps10, "27-point pointsize=", ps27)

        # set up menu bar
        self.menubar = wx.MenuBar()
        self.fileMenu = self.CreateMenu("&File", (
            ("Save Image...\tSHIFT-CTRL-S", "SaveImage", -1),
            ("Load Prefs",              "LoadPrefs", -1),
            ("Save Prefs",              "SavePrefs", -1),
            ("Load Data...",            "LoadData", -1),
            ("Save Data...",            "SaveData", -1),
            ("Load/Save Test Setup...\tCTRL-s", "LoadSaveTestSetup", -1),
            ("-",                        None, -1),
            ("Log Panel -->",            None, 2),
            ("logP Show",                "logPshow", -2),
            ("logP Hide",                "logPhide", -2),
            ("Close\tCTRL-w",            "OnClose", wx.ID_CLOSE),
            ("Quit\tCTRL-q",             "OnExit", wx.ID_EXIT),
        ))
        self.setupMenu = self.CreateMenu("&Setup", (
            ("Hardware Config Manager...", "ManageHWConfig", -1),
            ("Initial Cal Manager...",  "ManageInitCal", -1),
            ("PDM Calibration...",      "PDMCal", -1),
            ("DDS Tests. . .",          "ddsTests", -1),
            ("Cavity Filter Test ...",  "CavFiltTest", -1), # JGH 1/25/14
            ("Control Board Tests...",  "CtlBrdTests",-1),
            ("-",                       None, -1),
            ("Synthetic DUT...\tCTRL-D", "SynDUT", -1)
        ))
        self.sweepMenu = self.CreateMenu("&Sweep", [
            ("Sweep Parameters\tCTRL-F", "SetSweep", -1),
            ("Show Variables\tCTRL-I",  "ShowVars", -1),
            ("-",                        None, -1),
            ("Markers -->",              None, 4),
            ("Markers Independent",      "SetMarkers_Indep", -2),
            ("Markers P+,P- bounded by L,R", "SetMarkers_PbyLR", -2),
            ("Markers L,R bounded by P+", "SetMarkers_LRbyPp", -2),
            ("Markers L,R bounded by P-", "SetMarkers_LRbyPm", -2),
            ("Reference Lines -->",       None, 8)] +
            [("Set Reference Line %d...\tCTRL-%d" % (i, i), "SetRef", -1)
                for i in range(8)]
        )
        self.dataMenu = self.CreateMenu("&Data", (
            ("Save Graph Data",         "SaveGraphData", -1),
            ("Save Input Data",         "SaveInputData", -1),
            ("Save Intstalled Line Cal", "SaveInstalledLineCal", -1),
            ("-",                       None, -1),
            ("Dump Events",             "DumpEvents", -1),
            ("Save Debug Events",       "WriteEvents", -1),
        ))
        self.functionsMenu = self.CreateMenu("&Functions", (
            ("One Scan\tCTRL-E",        "DoExactlyOneScan", -1),
            ("One Step\tCTRL-T",        "DoOneStep", -1),
            ("Continue/Halt\tCTRL-R",   "OnContinueOrHalt", -1),
            ("-",                       None, -1),
            ("Filter Analysis...\tSHIFT-CTRL-F",  "AnalyzeFilter", -1),
            ("Component Meter...\tSHIFT-CTRL-C",  "ComponentMeter", -1),
            ("RLC Analysis...\tSHIFT-CTRL-R",     "AnalyzeRLC", -1),
            ("Coax Parameters...\tSHIFT-CTRL-X",  "CoaxParms", -1), # EON Jan 22, 2014
            ("Crystal Analysis...\tSHIFT-CTRL-K", "AnalyzeCrystal", -1),
            ("Step Attenuator Series...\tSHIFT-CTRL-S", "StepAttenuator", -1),
        ))
        self.operatingCalMenu = self.CreateMenu("&Operating Cal", (
            ("Perform Cal...\tCTRL-B",   "PerformCal", -1),
            ("Perform Update...\tCTRL-U","PerformCalUpd", -1), # EON Jan 10 2014
            ("-",                       None, -1),
            ("Reference -->",           None, 3),
            ("Reference To Band",       "SetCalRef_Band", -2),
            ("Reference To Baseline",   "SetCalRef_Base", -2),
            ("No Reference",            "SetCalRef_None", -2),
        ))
        self.modeMenu = self.CreateMenu("&Mode", (
            ("Spectrum Analyzer",       "SetMode_SA", -2),
            ("Spectrum Analyzer with TG", "SetMode_SATG", -2),
            ("VNA Transmission",        "SetMode_VNATran", -2),
            ("VNA Reflection",          "SetMode_VNARefl", -2),
        ))
        self.helpMenu = self.CreateMenu("&Help", (
            ("About",                   "OnAbout", wx.ID_ABOUT),
        ))
        self.SetMenuBar(self.menubar)
        self.closeMenuItem = self.fileMenu.FindItemById(wx.ID_CLOSE)
        

        self.logSplitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE)
        self.mainP = mainP = wx.Panel(self.logSplitter, style=wx.BORDER_SUNKEN)

        # define controls and panels in main spectrum panel
        sizer = wx.BoxSizer(wx.VERTICAL)
        from graphPanel import GraphPanel
        self.specP = specP = GraphPanel(mainP, self)
        sizer.Add(self.specP, 1, wx.EXPAND)
        botSizer = wx.BoxSizer(wx.HORIZONTAL)

        mark1Sizer = wx.BoxSizer(wx.VERTICAL)
        mark1Sizer.Add(wx.StaticText(mainP, -1, "Marker"), 0, wx.CENTER)
        self.markerNames = samples = ["None"] + \
                [str(i) for i in range(1, 7)] + ["L", "R", "P+", "P-"]
        cbox = wx.ComboBox(mainP, -1, "None", (0, 0), (80, -1), samples)
        self.markerCB = cbox
        mainP.Bind(wx.EVT_COMBOBOX, self.OnSelectMark, cbox)
        mark1Sizer.Add(cbox, 0, wx.ALL, 2)
        botSizer.Add(mark1Sizer, 0, wx.ALIGN_BOTTOM)

        mark2Sizer = wx.BoxSizer(wx.VERTICAL)
        btn = wx.Button(mainP, -1, "Delete", size=(90, -1))
        mainP.Bind(wx.EVT_BUTTON, self.OnDeleteMark, btn)
        mark2Sizer.Add(btn, 0, wx.ALL, 2)
        btn = wx.Button(mainP, -1, "Clear Marks", size=(90, -1))
        mainP.Bind(wx.EVT_BUTTON, self.ClearMarks, btn)
        mark2Sizer.Add(btn, 0, wx.ALL, 2)
        botSizer.Add(mark2Sizer, 0, wx.ALIGN_BOTTOM)

        mark3Sizer = wx.BoxSizer(wx.VERTICAL)
        mark3TSizer = wx.BoxSizer(wx.HORIZONTAL)
        btn = wx.Button(mainP, -1, "-", size=(25, -1))
        mainP.Bind(wx.EVT_BUTTON, self.OnDecMarkMHz, btn)
        mark3TSizer.Add(btn, 0, wx.ALL, 2)
        mark3TSizer.AddSpacer((0, 0), 1, wx.EXPAND)
        mark3TSizer.Add(wx.StaticText(mainP, -1, "MHz"), 0,
                    wx.ALIGN_CENTER_HORIZONTAL|wx.EXPAND|wx.ALL, 2)
        mark3TSizer.AddSpacer((0, 0), 1, wx.EXPAND)
        mark3Sizer.Add(mark3TSizer, 0, wx.EXPAND)
        self.mhzT = wx.TextCtrl(mainP, -1, str(self.markMHz), size=(100, -1))
        mark3Sizer.Add(self.mhzT, 0, wx.ALL, 2)
        btn = wx.Button(mainP, -1, "+", size=(25, -1))
        mainP.Bind(wx.EVT_BUTTON, self.OnIncMarkMHz, btn)
        mark3TSizer.Add(btn, 0, wx.ALL, 2)
        botSizer.Add(mark3Sizer, 0, wx.ALIGN_BOTTOM)
        btn = wx.Button(mainP, -1, "Enter", size=(50, -1))
        mainP.Bind(wx.EVT_BUTTON, self.OnEnterMark, btn)
        botSizer.Add(btn, 0, wx.ALIGN_BOTTOM|wx.ALL, 2)

        mark4Sizer = wx.BoxSizer(wx.VERTICAL)
        btn = wx.Button(mainP, -1, "Expand LR", size=(100, -1))
        mainP.Bind(wx.EVT_BUTTON, self.ExpandLR, btn)
        mark4Sizer.Add(btn, 0, wx.ALL, 2)
        btn = wx.Button(mainP, -1, "Mark->Cent", size=(100, -1))
        mainP.Bind(wx.EVT_BUTTON, self.OnMarkCent, btn)
        mark4Sizer.Add(btn, 0, wx.ALL, 2)
        botSizer.Add(mark4Sizer, 0, wx.ALIGN_BOTTOM)

        botSizer.AddSpacer((0, 0), 1, wx.EXPAND)
        stepSizer = wx.BoxSizer(wx.VERTICAL)
        btn = wx.Button(mainP, -1, "One Step", size=(90, -1))
        mainP.Bind(wx.EVT_BUTTON, self.DoOneStep, btn)
        stepSizer.Add(btn, 0, wx.ALL, 2)
        self.oneScanBtn = wx.Button(mainP, -1, "One Scan", size=(90, -1))
        mainP.Bind(wx.EVT_BUTTON, self.OnOneScanOrHaltAtEnd, self.oneScanBtn)
        stepSizer.Add(self.oneScanBtn, 0, wx.ALL, 2)
        ##self.oneScanBtn.SetToolTip(wx.ToolTip("Start one spectrum scan"))
        botSizer.Add(stepSizer, 0, wx.ALIGN_BOTTOM)

        goSizer = wx.BoxSizer(wx.VERTICAL)
        self.contBtn = wx.Button(mainP, -1, "Continue", size=(90, -1))
        mainP.Bind(wx.EVT_BUTTON, self.OnContinueOrHalt, self.contBtn)
        goSizer.Add(self.contBtn, 0, wx.ALL, 2)
        self.restartBtn = wx.Button(mainP, -1, "Restart", size=(90, -1))
        mainP.Bind(wx.EVT_BUTTON, self.OnRestartOrHalt, self.restartBtn)
        goSizer.Add(self.restartBtn, 0, wx.ALL, 2)
        botSizer.Add(goSizer, 0, wx.ALIGN_BOTTOM)

        sizer.Add(botSizer, 0, wx.EXPAND)
        mainP.SetSizer(sizer)

        # create a log text panel below and set it to log all output
        # (can't use LogTextCtrl-- it's broken)
        logP = wx.TextCtrl(self.logSplitter, \
                           style=wx.TE_MULTILINE|wx.TE_READONLY)
        logP.SetFont(wx.Font(fontSize, wx.MODERN, wx.NORMAL, wx.NORMAL))
        self.logP = logP
        lsVdim = int(p.get("logSplit", 0.8 * self.fVdim)) # On start
        self.logSplitter.SplitHorizontally(mainP, logP, lsVdim) # JGH 2/16/14
        self.logSplitter.name = "log"
        self.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGED, self.OnSashChanged)
       
        # redirect all output to a log file (disable this to see early errors)
        if 1:
            self.consoleStderr = sys.stderr
            logName = os.path.join(appdir, self.rootName)
            sys.stdout = Logger(logName, logP, self)
            sys.stderr = Logger(logName, logP, self)
        print (title, version, "log -- started", time.ctime())
        print ("Python", sys.version)
        print ("wx", wx.version(), "numpy", numpy.version.version)

        global msa
        self.msa = msa = MSA(self) # JGH MSA object is created here 1/25/14
        trace.msa = msa
        msaGlobal.SetMsa(msa)

        # initialize back end
        p.get("rbw", 300)
        p.get("wait", 10)
        p.get("sigGenFreq", 10.)
        p.get("tgOffset", 0.)
        p.get("planeExt", 3*[0.])
        if type(p.planeExt) != type([]) or len(p.planeExt) != 3:
            p.planeExt = 3*[0.]
        print ("planeExt=", p.planeExt)
        p.get("normRev", 0)
        p.get("isLogF", 0)
        p.get("continuous", False)
        p.get("sweepDir", 0)
        p.get("markerMode", Marker.MODE_INDEP)
        p.get("atten5", False)
        p.get("stepAttenDB", 0)
        p.get("switchPulse", 0) # JGH added Oct23
        p.get("cftest", 0)

        # initialize spectrum graph
        va0 = p.get("va0", -120.)
        va1 = p.get("va1", 0.)
        vb0 = p.get("vb0", -180.)
        vb1 = p.get("vb1", 180.)
        specP.vScales = vScales = []
        vai = p.get("vaTypeIndex", 1)
        vbi = p.get("vbTypeIndex", 2)
        vScales.append(VScale(vai, msa.mode, va1, va0, "dB"))
        vScales.append(VScale(vbi, msa.mode, vb1, vb0, "Deg"))
        self.refs = {}
        self.lastDoneDrawTime = 0
        self.btnScanMode = False    # True when buttons in scanning mode
        self.task = None
        self.smithDlg = None

        self.spectrum = None
        self.sweepDlg = None
        self.filterAnDlg = None
        self.compDlg = None
        self.tranRLCDlg = None
        self.coaxDlg = None # EON Jan 29, 2014
        self.crystalDlg = None
        self.stepDlg = None
        self.varDlg = None
        self.ReadCalPath()
        self.ReadCalFreq()
        self.Show(True)
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.OnTimer)
        self.Bind(EVT_UPDATE_GRAPH, self.OnTimer)
        self.yLock = threading.Lock()
        tmp = wx.Display().GetGeometry()
        self.screenWidth = tmp[2]

        # Initialize cavity filter test status # JGH 1/26/14
        self.cftest = 0

        # restore markers from preferences
        for attr, value in p.__dict__.items():
            if len(attr) > 9 and attr[:8] == "markers_":
                mm, mName, mAttr = string.split(attr, "_")
                mName = re.sub("p", "+", re.sub("m", "-", mName))
                m = specP.markers.get(mName)
                if not m:
                    specP.markers[mName] = m = Marker(mName, "", 0)
                setattr(m, mAttr, value)
                delattr(p, attr)

        # put a checkmark by the current mode in the Mode menu
        for i, item in enumerate(self.modeMenu.GetMenuItems()):
            item.Check(i == msa.mode)

        if p.get("logP",0) == 1:
            self.logPhide(None)
        else:
            self.logPshow(None)

        self.RefreshAllParms()

        # build an operating calibration file path, creating the dirs if needed
        cdir = os.path.join(appdir, "MSA_Info", "OperatingCal")
        self.baseCalFileDir = cdir
        if not os.path.exists(cdir):
            os.makedirs(cdir)
        # Start EON Jan 28, 2014
        self.bandCalFileName = os.path.join(cdir, "BandLineCal.s1p")
        self.baseCalFileName = os.path.join(cdir, "BaseLineCal.s1p")
        # read any operating calibration files
#        msa.bandCal = self.LoadCal(self.bandCalFileName)
#        msa.baseCal = self.LoadCal(self.baseCalFileName)
        msa.bandCal = None
        msa.baseCal = None
        # Start EON Jan 28, 2014

        # make one scan to generate a graph
        self.ScanPrecheck(True)
        # EON Following 3 lines added by Eric Nystrom
        if (p.get("dds3Track",False) or p.get("dds1Sweep",False)):
            dlg = DDSTests(self)
            dlg.Show()
        # Start EON Jan 22, 2014
        # Define functions available at the menu to each mode:
        self.funcModeList = [0] * 4
        self.funcModeList[MSA.MODE_SA] = ["filter","step"]
        self.funcModeList[MSA.MODE_SATG] = ["filter","component","step"]
        self.funcModeList[MSA.MODE_VNATran] = ["filter","component","rlc","crystal","group","step"]
        self.funcModeList[MSA.MODE_VNARefl] = ["component","rlc","coax","group","s21","step"]

        self.InitMode(msa.mode)
        # End EON Jan 22, 2014

    def wxYield(self):
        self.yLock.acquire(True)
        wx.Yield()
        self.yLock.release()

    #--------------------------------------------------------------------------
    # Create a menu of given items calling given routines.
    # An id == -2 sets item to be in a radio group.

    def CreateMenu(self, name, itemList):
        menu = wx.Menu()
        s = 0
        submenu = None
        subName = ""
        for itemName, handlerName, menuId in itemList:
            if itemName == "-":
                menu.AppendSeparator()
            else:
                if menuId == -1:
                    menuId = wx.NewId()
                    if s == 0:
                        item = menu.Append(menuId, itemName)
                    else:
                        item = submenu.Append(menuId, itemName)
                        s -= 1
                        if s == 0:
                            menu.AppendMenu(menuId, subName, submenu)
                elif menuId == -2:
                    menuId = wx.NewId()
                    if s == 0:
                        item = menu.AppendRadioItem(menuId, itemName)
                    else:
                        item = submenu.AppendRadioItem(menuId, itemName)
                        s -= 1
                        if s == 0:
                            menu.AppendMenu(menuId, subName, submenu)
                elif menuId > 0 and menuId < 10:
                    #print(">>>14679<<< Next " + str(menuId) + " items are part of a submenu")
                    subName = itemName
                    submenu = wx.Menu()
                    s = menuId
                    continue
                else:
                    item = menu.Append(menuId, itemName)
                if hasattr(self, handlerName):
                    self.Connect(menuId, -1, wx.wxEVT_COMMAND_MENU_SELECTED, \
                                getattr(self, handlerName))
                else:
                    item.Enable(False)
        self.menubar.Append(menu, name)
        return menu
    
    #--------------------------------------------------------------------------
    # derived from Menuitem class
    def SubMenu(self, parentMenu, menuId, text, menuHelp, kind, subMenu):
        pass

    #--------------------------------------------------------------------------
    # Change button names while scanning.

    def SetBtnsToScan(self, scanning):
        if scanning:
            self.contBtn.SetLabel("Halt")
            self.restartBtn.SetLabel("Halt")
            self.oneScanBtn.SetLabel("Halt at End")
        else:
            self.contBtn.SetLabel("Continue")
            self.restartBtn.SetLabel("Restart")
            self.oneScanBtn.SetLabel("One Scan")
        self.btnScanMode = scanning

    #--------------------------------------------------------------------------
    # Start capturing a spectrum.

    def ScanPrecheck(self, haltAtEnd):
        self.StopScanAndWait()
        ResetEvents()
        LogGUIEvent("ScanPrecheck")
        self.spectrum = None
        self.needRestart = False
        if msa.syndut: # JGH 2/8/14 syndutHook5
            if debug:
                print ("GETTING SYNTHETIC DATA")
            msa.syndut.GenSynthInput()
        p = self.prefs
        fStart = p.fStart
        fStop = p.fStop
        title = time.ctime()

        print ("----", title, "fStart=", mhzStr(fStart), "fStop=", \
             mhzStr(fStop), "----")
        self.wxYield()
        needsRefresh = False

        # get ready to redraw the grid if needed
        specP = self.specP
        specP.eraseOldTrace = False
        specP.markersActive = False
        if specP.h0 != fStart or specP.h1 != fStop or specP.title != title:
            LogGUIEvent("ScanPrecheck new graph range")
            specP._haveDrawnGrid = False
            specP.h0 = fStart
            specP.h1 = fStop
            specP.title = title
            needsRefresh = True

        # set up calibration table to use
        self.spectrum = None
        if not msa.calibrating:
            needsRefresh = self.CalCheck() # EON Jan 29, 2014

        if needsRefresh:
            self.RefreshAllParms()

        # tell MSA hardware backend to start a scan
        msa.ConfigForScan(self, p, haltAtEnd)

        LogGUIEvent("ScanPrecheck starting timer")
        # start display-update timer, given interval in ms
        self.timer.Start(msPerUpdate)
    #--------------------------------------------------------------------------

    #--------------------------------------------------------------------------
    # Check requested calibration level and set based on calibration present

    def CalCheck(self): # EON Jan 29, 2014
        p = self.prefs
        cal = (None, msa.baseCal, msa.bandCal)[p.calLevel]
        if cal:
            if ((msa.mode == MSA.MODE_VNATran and cal.oslCal) or \
                (msa.mode == MSA.MODE_VNARefl and (not cal.oslCal))):
                    self.SetCalLevel(0)
                    return True
            calF = cal.Fmhz
            # Start EON Jan 10 2014
            #calIsLogF = (calF[0] + calF[2])/2 != calF[1]
            calIsLogF = cal.isLogF
            # End EON Jan 10 2014
            ##print ("cal: %.20g %.20g %.20g %.20g" % \
            ##        (calF[0], fStart, calF[-1], fStop))
            ##print ("cal:", calF[0] == fStart, calF[-1] == fStop, \
            ##    cal.nSteps, p.nSteps, calIsLogF, p.isLogF)
            fStart = p.fStart
            fStop = p.fStop
            needsRefresh = False
            if round(calF[0] - fStart, 8) == 0 and \
                    round(calF[-1] - fStop, 8) == 0 and \
                    cal.nSteps == p.nSteps and calIsLogF == p.isLogF:
                # have a matching base or band calibration
                msa.calNeedsInterp = False
                # Start EON Jan 10 2014
                if cal.oslCal:
                    cal.installBandCal()
                # End EON Jan 10 2014
            elif p.calLevel > 0 and msa.baseCal and \
                        fStart >= msa.baseCal.Fmhz[0] and \
                        fStop <= msa.baseCal.Fmhz[-1]:
                # no match, but can use base
                msa.calNeedsInterp = True
                # Start EON Jan 10 2014
                if cal.oslCal:
                    msa.NewScanSettings(p)
                    cal.interpolateCal(msa._freqs)
                # End EON Jan 10 2014
                ##print ("Cal needs interpolation")
                if p.calLevel == 2:
                    self.SetCalLevel(1)
                    needsRefresh = True
            else:
                # no usable calibration at all
                ##print ("No usable calibration")
                if p.calLevel > 0:
                    self.SetCalLevel(0)
                    needsRefresh = True
            return needsRefresh

    #--------------------------------------------------------------------------
    # Stop any scanning and wait for all results to be updated.

    def StopScanAndWait(self):
        self.specP.markersActive = True
        if msa.IsScanning():
            msa.StopScan()
            self.WaitForStop()
        else:
            self.RefreshAllParms()

    #--------------------------------------------------------------------------
    # Wait for end of scan and all results to be updated.

    def WaitForStop(self):
        while msa.IsScanning() or not msa.scanResults.empty():
            self.wxYield()
            time.sleep(0.1)
        self.RefreshAllParms()

    #--------------------------------------------------------------------------
    # "One Step" button pressed.

    def DoOneStep(self, event=None):
        LogGUIEvent("DoOneStep")
        self.StopScanAndWait()
        if not self.needRestart:
            msa.WrapStep()
            msa.CaptureOneStep()
            msa.NextStep()

    #--------------------------------------------------------------------------
    # "One Scan"/"Halt at End" button pressed.

    def OnOneScanOrHaltAtEnd(self, event):
        LogGUIEvent("OnOneScanOrHaltAtEnd: scanning=%d" % msa.IsScanning())
        if msa.IsScanning():
            msa.haltAtEnd = True
        else:
            self.ScanPrecheck(True)

    #--------------------------------------------------------------------------
    # Ctrl-E: do exactly one scan.

    def DoExactlyOneScan(self, event=None):
        LogGUIEvent("DoExactlyOneScan: scanning=%d" % msa.IsScanning())
        self.StopScanAndWait()
        self.ScanPrecheck(True)

    #--------------------------------------------------------------------------
    # Continue/Halt button pressed.

    def OnContinueOrHalt(self, event):
        LogGUIEvent("OnContinueOrHalt")
        if msa.IsScanning():
            self.StopScanAndWait()
        elif not msa.HaveSpectrum() or self.needRestart:
            self.ScanPrecheck(False)
        else:
            msa.WrapStep()
            msa.haltAtEnd = False
            msa.ContinueScan()

    #--------------------------------------------------------------------------
    # Restart/Halt button pressed.

    def OnRestartOrHalt(self, event):
        LogGUIEvent("OnRestartOrHalt: scanning=%d step=%d" % \
            (msa.IsScanning(), msa.GetStep()))
        if msa.IsScanning(): # or self.needRestart:
            self.StopScanAndWait()
        else:
            self.ScanPrecheck(False)

    #--------------------------------------------------------------------------
    # Set Step Attenuator.

    def SetStepAttenuator(self, value):
        if msa.IsScanning():
            self.StopScanAndWait()
        from stepAtten import SetStepAttenuator
        SetStepAttenuator(value)

    #--------------------------------------------------------------------------
    # Timer tick: update display.

    def OnTimer(self, event):
        specP = self.specP
        assert wx.Thread_IsMain()
        ##LogGUIEvent("OnTimer")

        # draw any new scan data from the back end thread
        if not msa.scanResults.empty():
            spec = self.spectrum
            LogGUIEvent("OnTimer: have updates")
            if spec == None:
                spec = msa.NewSpectrumFromRequest(specP.title)
                self.spectrum = spec

            # add scanned steps to our spectrum, noting if they include
            # the last step
            includesLastStep = False
            while not msa.scanResults.empty():
                includesLastStep |= spec.SetStep(msa.scanResults.get())

            # move the cursor to the last captured step
            specP.cursorStep = spec.step
            # activate markers when at or passing last step
            specP.markersActive = includesLastStep
            if includesLastStep:
                specP.eraseOldTrace = True
                if msa.syndut:    # JGH 2/8/14 syndutHook6
                    msa.syndut.RegenSynthInput()
                if self.smithDlg and slowDisplay:
                    self.smithDlg.Refresh()
            self.DrawTraces()
            LogGUIEvent("OnTimer: all traces drawn, cursorStep=%d" % spec.step)
            if self.varDlg:
                self.varDlg.Refresh()

        # put Scan/Halt/Continue buttons in right mode
        if msa.IsScanning() != self.btnScanMode:
            self.SetBtnsToScan(msa.IsScanning())

        # write out any error messages from the backend
        while not msa.errors.empty():
            sys.stderr.write(msa.errors.get())

        # Component Meter continuous measurements, if active
        if self.task != None:
            self.task.AutoMeasure()

        ##LogGUIEvent("OnTimer: done")

    #--------------------------------------------------------------------------
    # Return the index for color i, adding it to the theme.vColor list if not
    # already there.

    def IndexForColor(self, i):
        p = self.prefs
        vColors = p.theme.vColors
        iNextColor = p.theme.iNextColor
        nColors = len(vColors)
        while len(vColors) <= i:
            vColors.append(vColors[iNextColor % nColors])
            iNextColor += 1
        p.theme.iNextColor = iNextColor
        return i

    #--------------------------------------------------------------------------
    # Copy the current and reference spectrums into the spectrum panel traces
    # and draw them.

    def DrawTraces(self):
        if debug:
            print ("DrawTraces")
        specP = self.specP
        specP.traces = {}
        p = self.prefs
        spec = self.spectrum
        if not spec:
            return
        LogGUIEvent("DrawTraces: %d steps" % len(spec.Sdb))

        # compute derived data used by various data types
        spec.f = spec.Fmhz
        #nSteps = len(f) - 1 # JGH (unused var nSteps)
        mode = p.mode
        includePhase = mode >= MSA.MODE_VNATran

        spec.isSeriesFix = p.get("isSeriesFix", False)
        spec.isShuntFix = p.get("isShuntFix", False)

        # set left (0) and right (1) vertical scale variables
        # and create potential traces for each (trva, trvb)
        types = traceTypesLists[mode]
        maxIndex = len(types)-1
        vScales = specP.vScales
        vs0 = vScales[0]
        p.vaTypeIndex = vaTypeIndex = min(vs0.typeIndex, maxIndex)
        p.va1 = vs0.top
        p.va0 = vs0.bot
        vaType = types[vaTypeIndex]

        # EON start of addition
        if spec.vaType != vaType:
            trva = vaType(spec, 0)
            trva.maxHold = vs0.maxHold
            trva.max = False
            if incremental:
                spec.vaType = vaType
                spec.trva = trva
        else:
            trva = spec.trva
        trva.iColor = self.IndexForColor(0)
        vs1 = vScales[1]
        p.vbTypeIndex = vbTypeIndex = min(vs1.typeIndex, maxIndex)
        p.vb1 = vs1.top
        p.vb0 = vs1.bot
        vbType = types[vbTypeIndex]
        if spec.vbType != vbType:
            trvb = vbType(spec, 1)
            trvb.maxHold = vs1.maxHold
            trvb.max = False
            if incremental:
                spec.vbType = vbType
                spec.trvb = trvb
        else:
            trvb = spec.trvb
        trvb.iColor = self.IndexForColor(1)

        # determine Mag and Phase traces, if any
        trM = trP = None
        if vaTypeIndex > 0:
            specP.traces[vaType.name] = trva
            if "dB" in trva.units:
                trM = trva
            if "Deg" in trva.units:
                trP = trva

        if vbTypeIndex > 0:
            specP.traces[vbType.name] = trvb
            if "dB" in trvb.units:
                trM = trvb
            if "Deg" in trvb.units:
                trP = trvb

        # if we have both Mag and Phase traces, point them to each other
        if trM and trP:
            trM.phaseTrace = trP
            trP.magTrace = trM
        # EON end of addition

        # draw any compatible reference traces
        for ri in self.refs.keys():
            ref = self.refs[ri]
            rsp = ref.spectrum
            if rsp.nSteps == spec.nSteps and rsp.Fmhz[0] == spec.Fmhz[0] \
                                         and rsp.Fmhz[-1] == spec.Fmhz[-1]:
                mathMode = ref.mathMode
                if trM and ri == 1 and ref.mathMode > 0:
                    # Ref 1 math applied to Mag, Phase
                    mData = trM.v
                    mRef = rsp.Sdb
                    if mathMode == 1:
                        mMath = mData + mRef
                    elif mathMode == 2:
                        mMath = mData - mRef
                    else:
                        mMath = mRef - mData
                    trM.v = dcopy.copy(mMath)
                    if includePhase and trP:
                        pData = trP.v
                        pRef = rsp.Sdeg
                        if mathMode == 1:
                            pMath = pData + pRef
                        elif mathMode == 2:
                            pMath = pData - pRef
                        else:
                            pMath = pRef - pData
                        trP.v = dcopy.copy(modDegree(pMath))
                else:
                    # Ref trace is displayed
                    refTypeM = ref.vScale.dataType
                    # vScales[] index 0 or 1 based on units (for now)
                    i = trvb.units and trvb.units == refTypeM.units
                    if not i:
                        i = 0
                    name = ref.name
                    refHasPhase = includePhase and refTypeM.units == "dB"
                    if refHasPhase:
                        # create ref's phase trace, with unique names for both
                        # (use continuous phase if that's being displayed)
                        continPhase = trP.units == "CDeg"
                        refTypeP = types[ref.vScale.typeIndex+1+continPhase]
                        refTrP = refTypeP(rsp, 1-i)
                        name = "%s_dB" % name
                        phName = "%s_%s" % (ref.name, trP.name.split("_")[1])
                    # create and assign name to ref's mag trace
                    specP.traces[name] = refTrM = refTypeM(rsp, i)
                    refTrM.name = name
                    refTrM.isMain = False
                    refTrM.iColor = self.IndexForColor(2 + 2*ri)
                    if refHasPhase:
                        # assign name to ref's phase trace
                        specP.traces[phName] = refTrP
                        refTrP.name = phName
                        refTrP.isMain = False
                        refTrP.iColor = self.IndexForColor(refTrM.iColor + 1)

        # enable drawing of spectrum (if not already)
        specP.Enable()

        # also show Smith chart if in reflection mode
        if msa.mode == MSA.MODE_VNARefl:
            if not self.smithDlg:
                from smithPanel import SmithDialog
                self.smithDlg = SmithDialog(self)
            elif not slowDisplay:
                self.smithDlg.Refresh()
        else:
            if self.smithDlg:
                self.smithDlg.Close()
                self.smithDlg = None

    #--------------------------------------------------------------------------
    # Open the Configuration Manager dialog box.

    def ManageHWConfig(self, event=None): # JGH This method heavily modified 1/20/14

        self.StopScanAndWait()
        p = self.prefs
        dlg = ConfigDialog(self)
        if dlg.ShowModal() == wx.ID_OK:

            # JGH modified 2/2/14
            p.PLL1type = dlg.cmPLL1.GetValue()
            p.PLL2type = dlg.cmPLL2.GetValue()
            p.PLL3type = dlg.cmPLL3.GetValue()
            p.PLL1phasepol = int(dlg.cmPOL1.GetValue()[1])  # JGH_001
            p.PLL2phasepol = int(dlg.cmPOL2.GetValue()[1])  # JGH_001
            p.PLL3phasepol = int(dlg.cmPOL3.GetValue()[1])  # JGH_001
##            p.PLL1mode = int(dlg.cmMOD1.GetValue()[0]) # JGH 2/7/14 Fractional mode not used
##            p.PLL3mode = int(dlg.cmMOD3.GetValue()[0]) # JGH 2/7/14 Fractional mode not used
            p.PLL1phasefreq = float(dlg.tcPhF1.GetValue())
            p.PLL2phasefreq = float(dlg.tcPhF2.GetValue())
            p.PLL3phasefreq = float(dlg.tcPhF3.GetValue())

            # JGH added 1/15/14
            gr = dlg.gridRBW
            RBWFilters = []
            for row in range(4):
                RBWfreq = float(gr.GetCellValue(row, 0))
                RBWbw = float(gr.GetCellValue(row,1))
                RBWFilters.append((RBWfreq, RBWbw))
            p.RBWFilters = RBWFilters
            # JGH NOTE: need to account here for existing RBW filters only
            msa.RBWFilters = p.RBWFilters

            gv = dlg.gridVF
            vFilterCaps = []
            for row in range(4):
                #Label = gv.GetRowLabelValue(row)
                uFcap = float(gv.GetCellValue(row,0)) # JGH 2/15/14
                vFilterCaps.append(uFcap)
            p.vFilterCaps = vFilterCaps

##          magTC = 10 * magCap
            # magTC: mag time constant in ms is based on 10k resistor and cap in uF
##          phaTC = 2.7 * phaCap
            # phaTC: phase time constant in ms is based on 2k7 resistor and cap in uF

            # JGH NOTE: need to account here for existing Video filters only
            msa.vFilterCaps = p.vFilterCaps

            # TOPOLOGY
            p.ADCtype = dlg.ADCoptCM.GetValue()

            p.CBopt = CBopt = dlg.CBoptCM.GetValue()
            
            if CBopt == "LPT": # JGH Only Windows does this
                p.winLPT = winUsesParallelPort = True
                # Windows DLL for accessing parallel port
                from ctypes import windll
                try:
                    windll.LoadLibrary(os.path.join(resdir, "inpout32.dll"))
                    cb = MSA_CB_PC()
                except WindowsError:
                    # Start up an application just to show error dialog
                    app = wx.App(redirect=False)
                    app.MainLoop()
                    dlg = ScrolledMessageDialog(None,
                                    "\n  inpout32.dll not found", "Error")
                    dlg.ShowModal()
                    sys.exit(-1)

            elif CBopt == "USB": # JGH Windows, Linux and OSX do this
                cb = MSA_CB_USB()
            elif CBopt == "RPI": # JGH RaspberryPi does this
                cb = MSA_RPI()
            elif CBopt == "BBB": # JGH BeagleBone does this
                cb =MSA_BBB()
            else:
                pass

            # JGH end of additions

            p.configWinPos = dlg.GetPosition().Get()
            LO1.appxdds =  p.appxdds1 =  float(dlg.dds1CentFreqBox.GetValue())
            LO1.ddsfilbw = p.dds1filbw = float(dlg.dds1BWBox.GetValue())
            LO3.appxdds =  p.appxdds3 =  float(dlg.dds3CentFreqBox.GetValue())
            LO3.ddsfilbw = p.dds3filbw = float(dlg.dds3BWBox.GetValue())
            msa.masterclock = p.masterclock = float(dlg.mastClkBox.GetValue())
            p.invDeg = float(dlg.invDegBox.GetValue())

    #--------------------------------------------------------------------------
    # Open the Calibration File Manager dialog box.

    def ManageInitCal(self, event):
        self.StopScanAndWait()
        p = self.prefs
        dlg = CalManDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            if dlg.dirty:
                dlg.SaveIfAllowed(self)
        self.ReadCalPath()
        self.ReadCalFreq()
        p.calManWinPos = dlg.GetPosition().Get()

    #--------------------------------------------------------------------------
    # Open the PDM Calibration dialog box.

    def PDMCal(self, event):
        self.StopScanAndWait()
        p = self.prefs
        dlg = PDMCalDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            p.invDeg = dlg.invDeg
        p.pdmCalWinPos = dlg.GetPosition().Get()

    #--------------------------------------------------------------------------
    # Open the DDS Tests dialog box # Eric Nystrom, new function created 12/15/2013

    def ddsTests(self, event): # Eric Nystrom, new function created 12/15/2013
        self.StopScanAndWait()
        #p = self.prefs        # JGH 2/10/14
        dlg = DDSTests(self)
        dlg.Show()

    #--------------------------------------------------------------------------
    # Open the Control Board Tests dialog box.

    def CtlBrdTests(self, event): # Eric Nystrom, new function created 12/15/2013
        self.StopScanAndWait()
        #p = self.prefs    # JGH 2/10/14
        dlg = CtlBrdTests(self)
        dlg.Show()

    #--------------------------------------------------------------------------
    # Open the Cavity Filter Test dialog box

    def CavFiltTest(self, event): # JGH 1/25/14, new function
        self.StopScanAndWait()
        # p = self.prefs    # JGH 2/10/14
        dlg = CavityFilterTest(self)
        dlg.Show()

#--------------------------------------------------------------------------

    # Handle buttons that manipulate markers.

    def OnIncMarkMHz(self, event):
        self.markMHz += 1.
        self.mhzT.SetValue(str(self.markMHz))

    def OnDecMarkMHz(self, event):
        self.markMHz -= 1.
        self.mhzT.SetValue(str(self.markMHz))

    def OnSelectMark(self, event):
        specP = self.specP
        markName = self.markerCB.GetValue()
        m = specP.markers.get(markName)
        if m:
            self.markMHz = m.mhz
            self.mhzT.SetValue(str(m.mhz))

    def OnEnterMark(self, event):
        self.markMHz = mhz = float(self.mhzT.GetValue())
        specP = self.specP
        markName = self.markerCB.GetValue()
        m = specP.markers.get(markName)
        if m:
            m.mhz = mhz
        else:
            traceName = specP.traces.keys()[0]
            specP.markers[markName] = Marker(markName, traceName, mhz)
        self.specP.FullRefresh()

    def OnDeleteMark(self, event):
        specP = self.specP
        markName = self.markerCB.GetValue()
        m = specP.markers.get(markName)
        if m:
            specP.markers.pop(markName)
            self.specP.FullRefresh()

    def ClearMarks(self, event=None):
        self.specP.markers = {}
        self.specP.FullRefresh()

    def ExpandLR(self, event=None):
        specP = self.specP
        p = self.prefs
        left = specP.markers.get("L")
        right = specP.markers.get("R")
        if left and right:
            p.fStart = left.mhz
            p.fStop = right.mhz
        self.RefreshAllParms()
        self.spectrum = None
        self.ScanPrecheck(True)

    def OnMarkCent(self, event):
        p = self.prefs
        fCent, fSpan = StartStopToCentSpan(p.fStart, p.fStop, p.isLogF)
        p.fStart, p.fStop = CentSpanToStartStop(self.markMHz, fSpan, p.isLogF)
        self.RefreshAllParms()
        self.spectrum = None
        self.ScanPrecheck(True)

    #--------------------------------------------------------------------------
    # Refresh parameter display in all open windows.

    def RefreshAllParms(self):
        p = self.prefs
        specP = self.specP
        if debug:
            print (">>>15359<<< RefreshAllParms", specP._isReady, self.refreshing)

##        # checkmark the current marker menu item in the Sweep menu
##        items = self.sweepMenu.Markers.GetSubMenu()
##        items[p.markerMode + 2].Check()
##        # checkmark the current menu item in the Operating Cal menu
##        items = self.operatingCalMenu.GetMenuItems()
##        items[5 - p.calLevel].Check() # EON Jan 10 2014

        # EON modified the following two checkmark tests 2/24/14
        # checkmark the current marker menu item in the Sweep menu
        for m in self.sweepMenu.GetMenuItems():
            if "markers" in m.GetText().lower():
                subItems = m.GetSubMenu().GetMenuItems()
                subItems[p.markerMode - 1].Check()
                break
        # checkmark the current menu item in the Operating Cal menu
        for m in self.operatingCalMenu.GetMenuItems():
            if "ref" in m.GetText().lower():
                subItems = m.GetSubMenu().GetMenuItems()
                subItems[2 - p.calLevel].Check()
                break
        for m in self.fileMenu.GetMenuItems():
            if "log" in m.GetText().lower():
                subItems = m.GetSubMenu().GetMenuItems()
                subItems[p.get("logP",0)].Check()
                break
        
        if (not specP or not specP._isReady) or self.refreshing:  # JGH
            return
        self.refreshing = True
        specP.FullRefresh()
        if self.sweepDlg:
            self.sweepDlg.UpdateFromPrefs()
        self.refreshing = False

    #--------------------------------------------------------------------------
    # Open the Synthetic DUT dialog box.

    def SynDUT(self, event=None): # JGH 2/8/14 syndutHook7
        global hardwarePresent, cb, msa
        if not msa.syndut:
            cb = MSA_CB()
            hardwarePresent = False
            from synDUT import SynDUTDialog
            msa.syndut = SynDUTDialog(self)
        else:
            msa.syndut.Raise()

    #--------------------------------------------------------------------------
    # Open the Sweep modeless dialog box.

    def SetSweep(self, event=None):
        if not self.sweepDlg:
            self.sweepDlg = SweepDialog(self)
        else:
            self.sweepDlg.Raise()
        self.sweepDlg.Show(True)

    #--------------------------------------------------------------------------
    # Open the Variables modeless info box.

    def ShowVars(self, event=None):
        if not self.varDlg:
            self.varDlg = VarDialog(self)
        else:
            self.varDlg.Raise()
        self.varDlg.Show(True)

    #--------------------------------------------------------------------------
    # Save an image of the graph to a file.

    def SaveImage(self, event):
        p = self.prefs
        context = wx.ClientDC(self.specP)
        memory = wx.MemoryDC()
        x, y = self.specP.ClientSize
        bitmap = wx.EmptyBitmap(x, y, -1)
        memory.SelectObject(bitmap)
        memory.Blit(0, 0, x, y, context, 0, 0)
        wildcard = "PNG (*.png)|*.png|JPEG (*.jpg)|*.jpg|BMP (*.bmp)|*.bmp"
        types = (".png", ".jpg", ".bmp")
        while True:
            imageDir = p.get("imageDir", appdir)
            dlg = wx.FileDialog(self, "Save image as...", defaultDir=imageDir,
                    defaultFile="", wildcard=wildcard, style=wx.SAVE)
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
            p.imageDir = os.path.dirname(path)
            chosenType = types[dlg.GetFilterIndex()]
            path = CheckExtension(path, self, types, chosenType)
            if not path:
                continue
            if ShouldntOverwrite(path, self):
                continue
            break
        base, ext = os.path.splitext(path)
        #type = wx.BITMAP_TYPE_PNG
        bmtype = wx.BITMAP_TYPE_PNG
        if ext == ".jpg":
            bmtype = wx.BITMAP_TYPE_JPEG    # JGH 2/10/14
        elif ext == ".bmp":
            bmtype = wx.BITMAP_TYPE_BMP # JGH 2/10/14
        print ("Saving image to", path)
        bitmap.SaveFile(path, bmtype)   # JGH 2/10/14

    #--------------------------------------------------------------------------
    # Load or save spectrum data to an s1p file.

    def LoadData(self, event):
        self.StopScanAndWait()
        p = self.prefs
        wildcard = "S1P (*.s1p)|*.s1p"
        dataDir = p.get("dataDir", appdir)
        dlg = wx.FileDialog(self, "Choose file...", defaultDir=dataDir,
                defaultFile="", wildcard=wildcard)
        if dlg.ShowModal() != wx.ID_OK:
            return
        path = dlg.GetPath()
        p.dataDir = os.path.dirname(path)
        print ("Reading", path)
        spec = self.spectrum = Spectrum.FromS1PFile(path)
        specP = self.specP
        specP.h0 = p.fStart = spec.Fmhz[0]  # EON Jan 10 2014
        specP.h1 = p.fStop  = spec.Fmhz[-1] # EON Jan 10 2014
        specP.eraseOldTrace = True
        p.nSteps = specP.cursorStep = spec.nSteps
        self.RefreshAllParms()
        self.DrawTraces()

    def SaveData(self, event=None, data=None, writer=None, name="Data.s1p"):
        self.StopScanAndWait()
        p = self.prefs
        if writer == None:
            writer = self.spectrum.WriteS1P
        if data == None:
            data = self.spectrum
        if data == None:
            raise ValueError("No data to save")
        name = os.path.basename(name)
        base, ext = os.path.splitext(name)
        wildcard = "%s (*%s)|*%s" % (ext[1:].upper(), ext, ext)
        while True:
            dataDir = p.get("dataDir", appdir)
            dlg = wx.FileDialog(self, "Save as...", defaultDir=dataDir,
                    defaultFile=name, wildcard=wildcard, style=wx.FD_SAVE)
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
            p.dataDir = os.path.dirname(path)
            path = CheckExtension(path, self, (ext))
            if not path:
                continue
            if ShouldntOverwrite(path, self):
                continue
            break
        if debug:
            print ("Saving data to", path)
        if data == self.spectrum:
            writer(path, self.prefs)
        else:
            writer(data, path)

    #--------------------------------------------------------------------------
    # Manually load and save preferences.

    def LoadPrefs(self, event=None):
        if self.prefs:
            self.StopScanAndWait()
        prefsName = os.path.join(appdir, self.rootName + ".prefs")
        self.prefs = p = Prefs.FromFile(prefsName)
        isLight = p.get("graphAppear", "Light") == "Light"
        p.theme = (DarkTheme, LightTheme)[isLight]
        p.theme.UpdateFromPrefs(p)

    def SavePrefs(self, event=None):
        p = self.prefs
        self.StopScanAndWait()
        for m in self.specP.markers.values():
            m.SavePrefs(p)
        p.theme.SavePrefs(p)
        p.save()

    #--------------------------------------------------------------------------
    # Open Test Setups Dialog box.

    def LoadSaveTestSetup(self, event=None):
        self.StopScanAndWait()
        p = self.prefs
        dlg = TestSetupsDialog(self)
        dlg.ShowModal()
        p.testSetupsWinPos = dlg.GetPosition().Get()

    #--------------------------------------------------------------------------
    # Set Markers mode.

    def SetMarkers_Indep(self, event=None):
        self.prefs.markerMode = Marker.MODE_INDEP
        self.RefreshAllParms()

    def SetMarkers_PbyLR(self, event=None):
        self.prefs.markerMode = Marker.MODE_PbyLR
        self.RefreshAllParms()

    def SetMarkers_LRbyPp(self, event=None):
        self.prefs.markerMode = Marker.MODE_LRbyPp
        self.RefreshAllParms()

    def SetMarkers_LRbyPm(self, event=None):
        self.prefs.markerMode = Marker.MODE_LRbyPm
        self.RefreshAllParms()

    #--------------------------------------------------------------------------
    # Open the Reference Line Specification dialog box.

    def SetRef(self, event):
        p = self.prefs
        refNum = event.Id - 600
        dlg = RefDialog(self, refNum)
        if dlg.ShowModal() == wx.ID_OK:
            mode = dlg.mode
            if mode == 0:
                # delete it
                if self.refs.has_key(refNum):
                    self.refs.pop(refNum)
            else:
                # create a new ref from current data
                spec = self.spectrum
                vScales = self.specP.vScales
                # get the units from both vertical scales
                bothU = [vs.dataType.units for vs in vScales]
                print ("bothU=", bothU)
                for i in range(2):
                    vScale = vScales[i]
                    # create a ref for each axis, unless the axes are
                    # (db, Deg), in which case we create one ref with both
                    if not (dlg.traceEns[i]) or \
                            (i == 1 and "dB" in bothU and \
                             ("Deg" in bothU or "CDeg" in bothU)):
                        if debug:
                            print ("SetRef not doing", refNum, i)
                        continue
                    ref = Ref.FromSpectrum(refNum, spec, vScale)
                    if mode == 2:
                        # if a fixed value, assign value
                        rsp = ref.spectrum
                        n = len(rsp.Fmhz)
                        rsp.Sdb = zeros(n) + \
                                         floatOrEmpty(dlg.valueABox.GetValue())
                        if msa.mode >= msa.MODE_VNATran:
                            rsp.Sdeg = zeros(n) + \
                                         floatOrEmpty(dlg.valueBBox.GetValue())
                    # assign trace width(s), name, and math mode
                    ref.name = dlg.nameBox.GetValue()
                    ref.aWidth = int(dlg.widthACB.GetValue())
                    if msa.mode >= msa.MODE_VNATran:
                        # ref for axis 0 may be both mag and phase traces
                        ref.bWidth = int(dlg.widthBCB.GetValue())
                    if ref.name == "":
                        ref.name = "R%d" % refNum
                    self.refs[refNum] = ref
                    if refNum == 1:
                        ref.mathMode = dlg.graphOptRB.GetSelection()

        self.DrawTraces()
        self.specP.FullRefresh()
        p.refWinPos = dlg.GetPosition().Get()

    #--------------------------------------------------------------------------
    # Open the Perform Calibration dialog box.

    def PerformCal(self, event=None):
        self.StopScanAndWait()
        p = self.prefs
        dlg = PerformCalDialog(self)
        if dlg.ShowModal() == wx.ID_OK:
            p.perfCalWinPos = dlg.GetPosition().Get()
            return True
        return False

    # Start EON Jan 10 2014
    #--------------------------------------------------------------------------
    # Open the Perform Calibration dialog box.

    def PerformCalUpd(self, event=None):
        self.StopScanAndWait()
        p = self.prefs
        dlg = PerformCalUpdDialog(self)
        if not dlg.error: # EON Jan 29, 2014
            if dlg.ShowModal() == wx.ID_OK:
                p.perfCalUpdWinPos = dlg.GetPosition().Get()

    # End EON Jan 10 2014

    #--------------------------------------------------------------------------
    # Set the calibration reference to Band, Base, or None.

    def SetCalRef_Band(self, event):
        # Start EON Jan 13 2014
        if msa.IsScanning():
            self.StopScanAndWait()
        # End EON Jan 13 2014
        if not msa.bandCal:
            self.PerformCal()
        if msa.bandCal:
            self.SetCalLevel(2)
        self.RefreshAllParms()

    def SetCalRef_Base(self, event):
        # Start EON Jan 13 2014
        if msa.IsScanning():
            self.StopScanAndWait()
        # End EON Jan 13 2014
        if not msa.baseCal:
            self.PerformCal()
        if msa.baseCal:
            self.SetCalLevel(1)
        self.RefreshAllParms()

    def SetCalRef_None(self, event):
        self.SetCalLevel(0)
        self.RefreshAllParms()

    #--------------------------------------------------------------------------
    # Set the calibration reference level, base, and band, keeping msa, prefs,
    # and data files in sync.

    def SetCalLevel(self, level):
        p = self.prefs
        msa.calLevel = p.calLevel = level
        if self.CalCheck(): # EON Jan 29, 2014
            self.RefreshAllParms()

    def SetBandCal(self, spectrum):
        msa.bandCal = spectrum
        if spectrum:
            msa.bandCal.WriteS1P(self.bandCalFileName, self.prefs,
                                 contPhase=True)
        else:
            # Start EON Jan 10 2014
            try:
                os.unlink(self.bandCalFileName)
            except:
                pass
            # End EON Jan 10 2014

    def SetBaseCal(self, spectrum):
        msa.baseCal = spectrum
        self.SaveCal(spectrum, self.baseCalFileName)

    def SetBandeCal(self, spectrum):
        msa.bandCal = spectrum
        self.SaveCal(spectrum, self.bandCalFileName)

    def SaveCal(self, spectrum, path):
        if spectrum:
            spectrum.WriteS1P(path, self.prefs, contPhase=True)
        elif os.path.exists(path):
            os.unlink(path)

    def LoadCal(self, path):
        if os.path.exists(path):
            cal = Spectrum.FromS1PFile(path) # EON Jan 29, 2014
            if cal == None:
                from oslCal import OslCal
                cal = OslCal.FromS1PFile(path)
            return cal
        else:
            return None

    def CopyBandToBase(self):
        if msa.bandCal != None:
            msa.baseCal = dcopy.deepcopy(msa.bandCal)
            msa.baseCal.WriteS1P(self.baseCalFileName, self.prefs, contPhase=True)

    #--------------------------------------------------------------------------
    # Read CalPath file for mag/phase linearity adjustment.

    def ReadCalPath(self):
        if debug:
            print ("10,665 Reading path calibration")
        self.StopScanAndWait()
        p = self.prefs
        directory, fileName = CalFileName(p.indexRBWSel+1)
        try:
            f = open(os.path.join(directory, fileName), "Ur")
            msa.magTableADC, msa.magTableDBm, msa.magTablePhase = \
                    CalParseMagFile(f)
            if debug:
                print (fileName, "read OK.")
        except:
            ##traceback.print_exc()
            if debug:
                print (fileName, "not found. Using defaults.")

    #--------------------------------------------------------------------------
    # Read CalFreq file for mag frequency-dependent adjustment.

    def ReadCalFreq(self):
        if debug:
            print ("Reading frequency calibration")
        self.StopScanAndWait()
        directory, fileName = CalFileName(0)
        try:
            f = open(os.path.join(directory, fileName), "Ur")
            msa.freqTableMHz, msa.freqTableDB = CalParseFreqFile(f)
            if debug:
                print (fileName, "read OK.")
        except:
            ##traceback.print_exc()
            if debug:
                print (fileName, "not found. Using defaults.")

    #--------------------------------------------------------------------------
    # Write data to a file.

    def SaveGraphData(self, event):
        self.SaveData(writer=self.specP.WriteGraph, name="GraphData.txt")

    def SaveInputData(self, event):
        self.SaveData(writer=self.spectrum.WriteInput, name="InputData.txt")

    def SaveInstalledLineCal(self, event):
        p = self.prefs
        if p.calLevel == 1:
            self.SaveData(data=msa.bandCal, writer=self.SaveCal,
                            name=self.bandCalFileName)
        elif p.calLevel == 2:
            self.SaveData(data=msa.baseCal, writer=self.SaveCal,
                            name=self.baseCalFileName)

    #--------------------------------------------------------------------------
    # Write debugging event lists to a file.

    def WriteEvents(self, event):
        msa.WriteEvents()

    def DumpEvents(self, event):
        msa.DumpEvents()

    #--------------------------------------------------------------------------
    # Show the Functions menu dialog boxes.

    def AnalyzeFilter(self, event):
        if not self.filterAnDlg:
            self.filterAnDlg = FilterAnalDialog(self)
        else:
            self.filterAnDlg.Raise()

    def ComponentMeter(self, event):
        if not self.compDlg:
            self.compDlg = ComponentDialog(self)
        else:
            self.compDlg.Raise()

    def AnalyzeRLC(self, event):
        if not self.tranRLCDlg:
            self.tranRLCDlg = AnalyzeRLCDialog(self)
        else:
            self.tranRLCDlg.Raise()
    # Start EON Jan 22, 2014
    def CoaxParms(self,event):
        if not self.coaxDlg: # EON Jan 29, 2014
            from coax import CoaxParmDialog
            self.coaxDlg = CoaxParmDialog(self)
        else:
            self.coaxDlg.Raise()
    # End EON Jan 22, 2014
    def AnalyzeCrystal(self, event):
        if not self.crystalDlg:
            self.crystalDlg = CrystAnalDialog(self)
        else:
            self.crystalDlg.Raise()

    def StepAttenuator(self, event):
        if not self.stepDlg:
            from stepAtten import StepAttenDialog
            self.stepDlg = StepAttenDialog(self)
        else:
            self.stepDlg.Raise()

    #--------------------------------------------------------------------------
    # Set the main operating mode.

    def SetMode_SA(self, event):
        p = self.prefs
        p.switchSG = 0   # JGH: Switch not implemented in software
        self.SetMode(msa.MODE_SA)

    def SetMode_SATG(self, event):
        p = self.prefs
        p.switchSG = 1   # JGH: Switch not implemented in software
        self.SetMode(msa.MODE_SATG)

    def SetMode_VNATran(self, event):
        # Start EON Jan 22, 2014
        p = self.prefs
        p.switchTR = 0   # JGH 11/25/13
        # End EON Jan 22, 2014
        self.SetMode(msa.MODE_VNATran)

    def SetMode_VNARefl(self, event):
        # Start EON Jan 22, 2014
        p = self.prefs
        p.switchTR = 1   # JGH 11/25/13
        # End EON Jan 22, 2014
        self.SetMode(msa.MODE_VNARefl)

    def SetMode(self, mode):
        self.StopScanAndWait()

        self.InitMode(mode) # EON Jan 22, 2014

        if debug:
            print ("Changed MSA mode to", msa.modeNames[mode])
        self.prefs.mode = mode
        msa.SetMode(mode)
        if self.spectrum:
            # reset trace type selections to default for this mode
            vScales = self.specP.vScales
            vs0 = vScales[0]
            vs0.typeIndex = 1
            vs0.dataType = dataType = traceTypesLists[mode][vs0.typeIndex]
            vs0.top = dataType.top
            vs0.bot = dataType.bot
            if vs0.top == 0 and vs0.bot == 0:
                vs0.AutoScale(self)
            vs1 = vScales[1]
            vs1.typeIndex = (0, 2)[mode >= MSA.MODE_VNATran]
            vs1.dataType = dataType = traceTypesLists[mode][vs1.typeIndex]
            vs1.top = dataType.top
            vs1.bot = dataType.bot
            if vs1.top == 0 and vs1.bot == 0:
                vs1.AutoScale(self)
        self.needRestart = True
        # Flip the TR switch
        self.RefreshAllParms()
        self.DrawTraces()

    # Start EON Jan 22, 2014
    # Initializes menu bar based on mode
    def InitMode(self,mode):
        p = self.prefs
##        if mode == msa.MODE_VNATran:
##            p.switchTR = 0   # JGH 11/25/13
##        if mode == msa.MODE_VNARefl:
##            p.switchTR = 1   # JGH 11/25/13

        p.calLevel = msa.calLevel = 0

        menuBar = self.MenuBar
        i = menuBar.FindMenu("Functions")
        funcMenu = menuBar.GetMenu(i)
##        items = funcMenu.GetMenuItems()    # JGH These 2 used only here, no need to rename
##        funcList = self.funcModeList[mode]
        skip = True
##        for m in items:
        for m in funcMenu.GetMenuItems():
            txt = m.GetText().lower()
            if len(txt) == 0:
                skip = False
            if skip:
                continue # Goes no next m
            found = False # Divider line found
##            for val in funcList:
            for val in self.funcModeList[mode]:
                if val in txt:
                    found = True
                    break
            m.Enable(found) # Enables items for the mode selected

        if mode == MSA.MODE_SA or mode == MSA.MODE_SATG:
            i = menuBar.FindMenu("Operating Cal")
            if i > 0:
                menuBar.Remove(i)
        else:
            if menuBar.FindMenu("Operating Cal") < 0:
                i = menuBar.FindMenu("Mode")
                if i > 0:
                    menuBar.Insert(i,self.operatingCalMenu,"Operating Cal")
    # End EON Jan 22, 2014

    #--------------------------------------------------------------------------
    # Handle a resize event of the main frame or log pane sash.

    def OnSizeChanged(self, event):
        p = self.prefs
        (self.fHdim, self.fVdim) = p.frameSize = self.GetSize()
        event.Skip()

    def OnSashChanged(self, event):
        p =  self.prefs
        sashWin = event.GetEventObject()
        p.logSplit = sashWin.GetSashPosition()
        #print("sashWin.name: ", sashWin.name) # JGH: sashWin.name is "log"
        setattr(self.prefs, sashWin.name + "Split", sashWin.GetSashPosition())
        event.Skip()

    #--------------------------------------------------------------------------
    # Hide/ reveal message panel

    def logPshow(self, event):
        p = self.prefs
        print("logSplit on show: ", p.logSplit)
        self.logSplitter.SetSashPosition(p.logSplit)
        self.logP.Show()
        p.logP = 0
        if event:
            self.RefreshAllParms()
            event.Skip()

    def logPhide(self, event):
        p = self.prefs
        print("logSplit on hide: ", p.logSplit)
        self.logSplitter.SetSashPosition(self.fVdim)
        self.logP.Hide()
        p.logP = 1
        if event:
            self.RefreshAllParms()
            event.Skip()

    #--------------------------------------------------------------------------
    # About dialog.

    def OnAbout(self, event):
        info = wx.AboutDialogInfo()
        info.Name = self.appName
        info.Version = version
        info.Description = "MSAPy is a portable interface for the " \
            "Modular Spectrum Analyzer."
        info.WebSite = ("http://sourceforge.net/projects/msapy/",
                        "MSAPy SourceForge page")
        info.Developers = ["Scott Forbes", "Sam Wetterlin", \
                           "Scotty Sprowls", "Jim Hontoria, W1JGH", \
                           "Eric Nystrom, W4EON"]
        wx.AboutBox(info)

    #--------------------------------------------------------------------------
    # Quitting.

    def OnExit(self, event):
        if msa.syndut:    # JGH syndutHook8
            msa.syndut.Close()
        if self.smithDlg:
            self.smithDlg.Close()
        print ("Exiting")
        self.SavePrefs()
        print ("Exiting2")
        self.Destroy()

#==============================================================================
    # CAVITY FILTER TEST # JGH 1/26/14

class CavityFilterTest(wx.Dialog):
    def __init__(self, frame):
        self.frame = frame
        p = self.prefs = frame.prefs
        framePos = frame.GetPosition()
        pos = p.get("CavFiltTestsWinPos", (framePos.x + 100, framePos.y + 100))
        wx.Dialog.__init__(self, frame, -1, "Cavity Filter Test", pos, \
                            wx.DefaultSize, wx.DEFAULT_DIALOG_STYLE)
        self.cftest = p.cftest = 0
        sizerV = wx.BoxSizer(wx.VERTICAL)
        # panel = wx.Panel(self, -1) # JGH 2/10/14 panel not used
        st = wx.StaticText(self, -1, \
        "\nScans around zero in 0.1 MHz increments--e.g. span=10, steps=100, "\
        "span=10 to 50, or steps=span/0.1. User sets up scan, restarts, halts, "\
        "and clicks the Test Cavity Filter button. "\
        "The software will command the PLO2 to maintain an offset from PLO1 by "\
        "exactly the amount of the final IF, that is, PLO2 will always be equal"\
        "to PLO1+IF. The PLL2 Rcounter buffer is commanded one time, to assure pdf "\
        "will be 100 KHz; this is done during Init after 'Restart'. The PLO2 N"\
        "counter buffer is commanded at each step in the sweep. The actual frequency that is "\
        "passed through the Cavity Filter is the displayed frequency plus 1024 MHz. "\
        "The Cavity Filter sweep limitations are: \n"\
        "   -the lowest frequency possible is where PLO 1 cannot legally command\n"\
        "   -(Bcounter=31, appx 964 MHz)\n"\
        "   -(PLO1 or PLO2 bottoming out at 0V is also limit, likely below 964 MHz)\n"\
        "   -the highest frequency possible is where PLO2 tops out (vco volts "\
        "near 5v, somewhere between 1050 to 1073 MHz)\n"\
        "Sweep can be halted at any time and Sweep Parameters can be changed, "\
        "then click Continue or Restart.\n"\
        "The Cavity Filter Test window must be closed before MSA returns to normal. "\
        "Then click 'Restart'.")
        st.Wrap(600)
        c = wx.ALIGN_CENTER
        sizerV.Add(st, 0, c|wx.ALL, 10)

        btn = wx.Button(self, -1, "Test Cavity Filter")
        btn.Bind(wx.EVT_BUTTON, self.OnCFTest)
        sizerV.Add(btn, 0, c|wx.ALL, 5)

        # Cancel and OK buttons
        butSizer = wx.BoxSizer(wx.HORIZONTAL)
        butSizer.Add((0, 0), 0, wx.EXPAND)
        btn = wx.Button(self, wx.ID_CANCEL)
        butSizer.Add(btn, 0, wx.ALL, 5)
        btn = wx.Button(self, -1, "Close")
        btn.Bind(wx.EVT_BUTTON, self.CloseCavityFilterTest)
        butSizer.Add(btn, 0, wx.ALL, 5)
        sizerV.Add(butSizer, 0, wx.ALIGN_RIGHT|wx.ALIGN_BOTTOM|wx.ALL, 10)

        self.SetSizer(sizerV)
        sizerV.Fit(self)
        if pos == wx.DefaultPosition:
            self.Center()
    #------------------------------------------------------------------------
    def OnCFTest(self, event=None): # JGH 2/3/14 Fully modified
        p = self.frame.prefs
        if self.cftest == 1 and msa.IsScanning():
            self.frame.StopScanAndWait()
        p.cftest = 1
        self.Refreshing = False
        self.enterPLL2phasefreq = p.PLL2phasefreq
        LO2.PLLphasefreq = p.PLL2phasefreq = .1 # JGH 2/5/14
        # Goto restart
        self.frame.ScanPrecheck(False) # JGH True is for HaltAtEnd

    #------------------------------------------------------------------------

    def CloseCavityFilterTest(self, event=None):
        # will come here when Cavity Filter Test Window is closed
        p = self.frame.prefs
        p.cftest = 0
        LO2.PLLphasefreq = p.PLL2phasefreq = self.enterPLL2phasefreq # JGH 2/5/14
        p.CavFiltTestWinPos = self.GetPosition().Get()
        self.Destroy()

    # JGH ends

#------------------------------------------------------------------------------

import trace
from trace import traceTypesLists

#==============================================================================
# Start up application.

class MSAApp(wx.App):
    def OnInit(self):
        name = os.path.splitext(os.path.split(sys.argv[0])[1])[0]
        appPath = os.path.split(sys.argv[0])[0].split(os.path.sep)
        for f in appPath:
            if ".app" in f:
                name = f[:-4]
                break
        MSASpectrumFrame(None, name)
        return True

    def ProcessEvent(self, event):
        if debug:
            print ("ProcessEvent")
        event.Skip()
