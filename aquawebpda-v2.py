#!/usr/bin/env python
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
# coding=utf-8

"""Simulates Aqualink PDA remote with a RS485 interface."""

import string
import serial
import struct
import threading
import sys
import time
import socket
import os
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import cgi
import logging
import re

# Configuration
RS485Device = "/dev/ttyUSB0"    # RS485 serial device to be used
PORT = 80   # port for web server
ID = "60"   # address of PDA remote to emulate
debugData = False 

# ASCII constants
NUL = '\x00'
DLE = '\x10'
STX = '\x02'
ETX = '\x03'

logging.basicConfig(filename='aw.log',filemode='a',format='%(message)s',level=logging.DEBUG)

INDEXHTML = """
<html>
<head>
<title>Aqualink PDA Pool Controller</title>
<script language="Javascript">

if (window.XMLHttpRequest) {
    var xmlHttpReqKey = new XMLHttpRequest();
    var xmlHttpReqScreen = new XMLHttpRequest();
} else {
    var xmlHttpReqKey = new ActiveXObject("Microsoft.XMLHTTP"); 
    var xmlHttpReqScreen = new ActiveXObject("Microsoft.XMLHTTP");
}

function screen() {
    xmlhttpPost(xmlHttpReqScreen, "/screen.cgi", "", "screen");
}

function sendkey(key) {
    xmlhttpPost(xmlHttpReqKey, "/key.cgi", "key="+key);
}

function xmlhttpPost(xmlReq, strURL, params, update) {
    xmlReq.open('POST', strURL, true);
    xmlReq.setRequestHeader("Content-type","application/x-www-form-urlencoded");
    xmlReq.send(params);
    if (update != "") {
      xmlReq.onreadystatechange = function() {
        if (xmlReq.readyState == 4) {
            updatepage(xmlReq.responseText, update);
        }
      }
    }
    xmlReq.send();
}

function updatepage(str, div){
    document.getElementById(div).innerHTML = str;
    setTimeout(window[div](), 250);
}
</script>

<style type="text/css">
<!--
body {
    background-color: #0066ff;
}

button {
    font-size: 4vw; 
    width: 21vw; 
    height: 14vw;
    background-color: #cce0ff;
}

button.command {
    background-color: #ffffe6;
}

td {
    padding: 8px;
    text-align: center;
-->
</style>

</head>
<body onload="screen();">

<table style="width: 100%;">
<tr><td align="center" style="border:1px solid black; font-size: 5vw; background-color: #cce0ff;"><div id="screen"></div></font></td></tr>
<tr><td>
    <table style="width: 100%;">
        <tr>
            <td><button onclick="sendkey('back');">Back</button</td>
            <td><button onclick="sendkey('up');">Up</button></td>
            <td><button onclick="sendkey('select');">Select</button></td>
        </tr>
        <tr>
            <td><button onclick="sendkey('pgup');">1</button></td>
            <td><button onclick="sendkey('down');">Down</button></td>
            <td><button onclick="sendkey('pgdn');">2</button></td>
        </tr>
        <tr><td>&nbsp;<br></td></tr>
        <tr>
            <td><button class="command" onclick="sendkey('poolmode');">Pool Mode</button></td>
            <td><button class="command" onclick="sendkey('poollight');">Pool Light</button></td>
            <td><button class="command" onclick="sendkey('poolheater');">Pool Heater</button></td>
        </tr>
        <tr>
            <td><button class="command" onclick="sendkey('spamode');">Spa Mode</button></td>
            <td><button class="command" onclick="sendkey('spalight');">Spa Light</button></td>
            <td><button class="command" onclick="sendkey('spaheater');">Spa Heater</button></td>
        </tr>
        <tr>
            <td><button class="command" onclick="sendkey('cleaner');">Cleaner</button></td>
            <td><button class="command" onclick="sendkey('blower');">Blower</button></td>
            <td><button class="command" onclick="sendkey('alloff');">All Off</button></td>
        </tr>
    </table>
</td></tr>
</table>
</body>
</html>
"""


class webHandler(BaseHTTPRequestHandler):
    """CGI and dummy web page handler to interface to control objects."""
    screen = None
    
    #Handler for the GET requests
    def do_GET(self):
        """HTTP GET handler, only the html files allowed."""
        if self.path == "/":
            self.path = "/index.html"
        # We only serve some static stuff
        if self.path.startswith("/index.html"):
            mimetype = 'text/html'
            ret = INDEXHTML
            self.send_response(200)
            self.send_header('Content-Type', mimetype)
            self.end_headers()
            self.wfile.write(ret)
        else:
            self.send_error(404, 'File Not Found: %s' % self.path)

    # don't log POSTs
    def log_message(self, format, *args):
        return
            
    def do_POST(self):
        """HTTP POST handler.  CGI "scripts" handled here."""
        ctype, pdict = cgi.parse_header(self.headers.getheader('content-type'))
        try:
            if ctype == 'multipart/form-data':
                postvars = cgi.parse_multipart(self.rfile, pdict)
            elif ctype == 'application/x-www-form-urlencoded':
                length = int(self.headers.getheader('content-length'))
                postvars = cgi.parse_qs(self.rfile.read(length), keep_blank_values=1)
            else:
                postvars = {}
        except:
            postvars = {}
        if (self.path.startswith("/key.cgi") or
                self.path.startswith("/screen.cgi")):
            mimetype = 'text/html'
            ret = ""
            if self.path.startswith("/key.cgi"):
                if 'key' in postvars:
                    key = postvars['key'][0]
                    self.screen.sendKey(key)
                ret = "<html><head><title>key</title></head><body>"+key+"</body></html>\n"
            elif self.path.startswith("/screen.cgi"):
                ret = self.screen.html()
            self.send_response(200)
            self.send_header('Content-Type', mimetype)
            self.end_headers()
            self.wfile.write(ret)
        else:
            self.send_error(404, 'File Not Found: %s' % self.path)


class MyServer(HTTPServer):
    """Override some HTTPServer procedures to allow instance variables and timeouts."""
    def serve_forever(self, screen):
        """Store the screen object and serve until end of times."""
        self.RequestHandlerClass.screen = screen 
        HTTPServer.serve_forever(self)
    def get_request(self):
        """Get the request and client address from the socket."""
        self.socket.settimeout(1.0)
        result = None
        while result is None:
            try:
                result = self.socket.accept()
            except socket.timeout:
                pass
        result[0].settimeout(1.0)
        return result


def startServer(screen):
    """HTTP Server implementation, to be in separate thread from main code."""
    try:
        webServer = MyServer(('', PORT), webHandler)
        # Wait forever for incoming http requests
        webServer.serve_forever(screen)
    except KeyboardInterrupt:
        print '^C received, shutting down the web server'
        webServer.shutdown()

class Screen(object):
    """Emulates the Aqualink PDA remote control unit."""
    W = 16
    H = 10 
    lock = None
    nextAck = "00"
    poolmode = spamode = heater = poolheater = spaheater = pump = pumprpm = pumpwatts = tempair = tempwater = 0
    macro = ""
    macroback = macroscroll = 0

    def __init__(self):
        """Set up the instance"""
        self.screen = self.W * [self.H * " "]
        self.invert = {'line':-1, 'start':-1, 'end':-1}
        self.currentline = -1
        self.lock = threading.Lock()

    def cls(self):
        """Clear the screen."""
        self.lock.acquire()
        try:
            for i in range(0, self.H):
                self.screen[i] = " "
            self.invert['line'] = -1
        finally:
            self.lock.release()
        self.currentline = -1

    def scroll(self, start, end, direction):
        """Scroll screen up or down per controller request."""
        self.lock.acquire()
        try:
            if direction == 255:  #-1
                for x in range(start, end):
                    self.screen[x] = self.screen[x+1]
                self.screen[end] = self.W*" "
            elif direction == 1:  # +1
                for x in range(end, start, -1):
                    self.screen[x] = self.screen[x-1]
                self.screen[start] = self.W*" "
        finally:
            self.lock.release()

    def updateStatus(self, text):
        # check for ON or ENA on main menu
        searchObj = re.search("POOL MODE\s+(.*)",text)
        if searchObj:
            if searchObj.group(1) == "ON":
                self.poolmode = 1
                self.spamode = 0
                self.pump = 1
            else:
                self.poolmode = 0

        searchObj = re.search("SPA MODE\s+(.*)",text)
        if searchObj:
            if searchObj.group(1) == "ON":
                self.spamode = 1
                self.poolmode = 0
                self.pump = 1 
            else:
                self.spamode = 0

        searchObj = re.search("POOL HEATER\s+(.*)",text)
        if searchObj:
            if searchObj.group(1) == "ENA":
                self.poolheater = 1
                self.pump = 1
            else:
                self.poolheater = 0


        searchObj = re.search("SPA HEATER\s+(.*)",text)
        if searchObj:
            if searchObj.group(1) == "ENA":
                self.spaheater = 1
                self.pump = 1
            else:
                self.spaheater = 0

        # get temps - first is air, second is water (but only shows if pump is running)
        searchObj = re.search("^\s*(\d+)`\s+((\d+)`|)",text)
        if searchObj:
            if searchObj.group(1): self.tempair = searchObj.group(1)
            if searchObj.group(3): 
                self.tempwater =  searchObj.group(3)
            else:
                self.tempwater = 0

        # get pump info
        searchObj = re.search("^\s+RPM\:\s+(\d{1,4})",text)
        if searchObj:
            if searchObj.group(1): self.pumprpm = searchObj.group(1)
            
        searchObj = re.search("^\s+WATTS\:\s+(\d{1,4})",text)
        if searchObj:
            if searchObj.group(1): self.pumpwatts = searchObj.group(1)

        if self.poolheater == 1 or self.spaheater == 1:
            self.heater = 1
        else:
            self.heater = 0

        if self.poolmode == 0 and self.spamode == 0: self.pump = self.pumprpm = self.pumpwatts = self.heater = 0  # if both pool and spa are off, assume pump and heater is as well


    def writeLine(self, line, text):
        """"Controller sent new line for screen."""
        self.lock.acquire()
        try:
            self.screen[line] = text + self.W*" "
            self.screen[line] = self.screen[line][:self.W]
        finally:
            self.lock.release()

    def invertLine(self, line):
        """Controller asked to invert entire line."""
        self.lock.acquire()
        try:
            self.invert['line'] = line
            self.invert['start'] = 0
            self.invert['end'] = self.W
        finally:
            self.lock.release()
        self.currentline = line

    def invertChars(self, line, start, end):
        """Controller asked to invert chars on a line."""
        self.lock.acquire()
        try:
            self.invert['line'] = line
            self.invert['start'] = start
            self.invert['end'] = end
        finally:
            self.lock.release()


    def html(self):
        """Return the screen as a HTML element (<PRE> assumed)"""
        self.lock.acquire()
        try:
            ret = "<pre>"
            for x in range(0, self.H): 
                if x == self.invert['line']:
                    for y in range(0, self.W):
                        if y == self.invert['start']:
                            ret += "<span style=\"background-color: #FFFF00\"><b>"
                        ret += self.screen[x][y:y+1]
                        if y == self.invert['end']:
                            ret += "</b></span>"
                    if self.invert['end'] == self.W:
                        ret += "</b></span>"
                    ret += "\n"
                else:
                    ret += self.screen[x] + "\n"
            ret += "</pre>"
        finally:
            self.lock.release()
        return ret

    def sendAck(self, i, ret):
        """Controller talked to us - send back macro, last keypress, or an empty ack."""
        ackstr = "4000"

        # if a macro button has been pressed, process it
        if len(self.macro) >= 1:
           
            if ret['cmd'] == "02": 
               
                # loop through macro steps and current screen to see if anything is here, in which case skip a few macro steps
                macrocount = 0
                for macroitem in self.macro:
                    if filter(re.compile(macroitem).search,self.screen[1:-1]):
                            del self.macro[0:macrocount]
                            break
                    macrocount += 1
               
                searchMore = re.search("MORE",self.screen[9])  # search for "MORE" line (scrolling menu)
                searchCurrentline = re.search(self.macro[0],self.screen[self.currentline])  # search current line for target
                searchScreen = filter(re.compile(self.macro[0]).search,self.screen[1:-1]) # search screen for first macro
                                                                 
                #move up if it looks quicker to get to target command
                movecmd = "4006" # by default move down
                if searchScreen:
                    foundindex = self.screen.index(searchScreen[0])
                    if self.currentline < foundindex:
                        movecmd = "4005"

                if searchScreen or searchMore:   # is current macro anywhere on the screen or can we scroll for more options?
                    if searchCurrentline: # target found, select
                        ackstr = "4004" 
                        del self.macro[0]
                        self.macroscroll = 0
                    else:
                        if self.macroscroll > 13: # can't find it here, move back to look on previous screen
                            self.macroscroll = 0
                            ackstr = "4002"  # move back to look on previous screen
                            self.macroback += 1
                        else:
                            ackstr = movecmd  # target is on this screen, move up or down 
                            self.macroscroll += 1
                else:
                    self.macroscroll = 0
                    if self.macroback >= 3:  # moved back too many times, give up
                        del self.macro[0]
                        self.macroback = 0
                    else:
                        ackstr = "4002"  # move back to look on previous screen
                        self.macroback += 1

                #brute force - string of button pushes only
                #ackstr = "40" + self.macro[0]
                #del self.macro[0]
        else:
            ackstr = "40" + self.nextAck
            
        i.sendMsg( (chr(0), chr(1), ackstr.decode("hex")) )
        self.nextAck = "00"

    def setNextAck(self, nextAck):
        """Set the value we will send on the next ack, but don't send yet."""
        self.nextAck = nextAck

    def sendKey(self, key):
        """Send a key (text) on the next ack."""
        keyToAck = { 'up':"06", 'down':"05", 'back':"02", 'select':"04", 'but1':"01", 'but2':"03" }
        
        if key == "cleaner":
            self.macro = ["EQUIPMENT","CLEANER"]
        elif key == "poollight":
            self.macro = ["EQUIPMENT","POOL LIGHT"]
        elif key == "spalight":
            self.macro = ["EQUIPMENT","SPA LIGHT"]
        elif key == "poolmode":
            self.macro = ["POOL MODE"]
        elif key == "spamode":
            self.macro = ["SPA MODE"]
        elif key == "poolheater":
            self.macro = ["POOL HEATER"]
        elif key == "spaheater":
            self.macro = ["SPA HEATER"]
        elif key == "alloff":
            self.macro = ["EQUIPMENT","ALL OFF"]
        elif key == "blower":
            self.macro = ["EQUIPMENT","AIR BLOWER"]
        elif key == "status":
            log("poolmode="+str(self.poolmode)+\
                    " spamode="+str(self.spamode)+\
                    " heater="+str(self.heater)+\
                    " pump="+str(self.pump)+\
                    " pumprpm="+str(self.pumprpm)+\
                    " pumpwatts="+str(self.pumpwatts)+\
                    " air="+str(self.tempair)+\
                    " water="+str(self.tempwater))
            log("current line = " + str(self.currentline) +" - [" + self.screen[self.currentline] + "]")
            self.screen[0] = "Status!"
        else:
            if key in keyToAck.keys():
                self.setNextAck(keyToAck[key])

    def processMessage(self, ret, i):
        """Process message from a controller, updating internal state."""

        """ known commands:
            00 = probe
            02 = status/keepalive
            04 = write line
            05 = initial handshake (?)
            08 = invert line
            09 = clear screen
            0f = scroll screen
            10 = invert some of line
            1b = unknown/initial screen (?)
        """

        if ret['cmd'] == "09":  # Clear Screen
            self.cls()
        elif ret['cmd'] == "0f":  # Scroll Screen
            start = ord(ret['args'][:1])
            end = ord(ret['args'][1:2])
            direction = ord(ret['args'][2:3])
            self.scroll(start, end, direction)
        elif ret['cmd'] == "04":  # Write a line
            line = ord(ret['args'][:1])
            if line == 64: line = 0  # PDA: time (hex=40)
            if line == 130: line = 2  # PDA: temp (hex=82)
            offset = 1
            text = ""
            while (ret['args'][offset:offset+1].encode("hex") != "00") and (offset < len(ret['args'])):
                text += ret['args'][offset:offset+1]
                offset = offset + 1
            self.writeLine(line, text)
            self.updateStatus(text)
        elif ret['cmd'] == "00":  # probe
            pass
        elif ret['cmd'] == "1b":  # boot message
            pass #do nothing
        elif ret['cmd'] == "02":  # status/keepalive
            pass #do nothing; args may be useful?
        elif ret['cmd'] == "08":  # Invert an entire line
            self.invertLine( ord(ret['args'][:1]) )
        elif ret['cmd'] == "10":  # Invert just some chars on a line
            self.invertChars( ord(ret['args'][:1]), ord(ret['args'][1:2]), ord(ret['args'][2:3]) )
        else:
            log("UNKNOWN MESSAGE: dest=" +ret['dest'] + " cmd=" + ret['cmd'] + " args=" + ret['args'].encode("hex"))
        
        self.sendAck(i,ret)

def log(*args):
    message = "%-16s " % args[0]
    for arg in args[1:]:
        message += arg.__str__() + " "
    logmsg = time.asctime(time.localtime()) + ": " + message
    print(logmsg)
    logging.info(logmsg)

class Interface(object):
    """ Aqualink serial interface """

    def __init__(self, theName):
        """Initialization.
        Open the serial port and find the start of a message."""
        self.name = theName
        self._open()
        self.msg = "\x00\x00"
        # skip bytes until synchronized with the start of a message
        while (self.msg[-1] != STX) or (self.msg[-2] != DLE):
            self.msg += self.port.read(1)
        self.msg = self.msg[-2:]

    def _open(self):
        """Try and connect to the serial port, if it exists.  If not, then
        add a small delay to avoid CPU hogging"""
        try:
            if not os.path.exists(RS485Device):
                time.sleep(1)
            self.port = serial.Serial(RS485Device, baudrate=9600, 
                                  bytesize=serial.EIGHTBITS, 
                                  parity=serial.PARITY_NONE, 
                                  stopbits=serial.STOPBITS_ONE,
                                  timeout=0.1)
        except:
            self.port = None
         
        
    def readMsg(self):
        """ Read the next valid message from the serial port.
        Parses and returns the destination address, command, and arguments as a 
        tuple."""
        if (self.port == None):
            self._open()  # Try and re-open port
        if (self.port == None):  # We failed, return garbage
            return {'dest':"ff", 'cmd':"ff", 'args':""}

        while True:                                         
            dleFound = False
            # read what is probably the DLE STX
            try:
                self.msg += self.port.read(2)
            except serial.SerialException:
                self.msg += chr(0) + chr(0)
                self._open()
            while len(self.msg) < 2:
                self.msg += chr(0)
            while (self.msg[-1] != ETX) or (not dleFound) or (len(self.msg)>128):  
                # read until DLE ETX
                try:
                    if (self.port == None):
                        return {'dest':"ff", 'cmd':"ff", 'args':""}
                    self.msg += self.port.read(1)
                except serial.SerialException:
                    self.msg += chr(0)
                    self._open()
                if self.msg[-1] == DLE:                     
                    # \x10 read, tentatively is a DLE
                    dleFound = True
                if (self.msg[-2] == DLE) and (self.msg[-1] == NUL) and dleFound: 
                    # skip a NUL following a DLE
                    self.msg = self.msg[:-1]
                    # it wasn't a DLE after all
                    dleFound = False                        
            # skip any NULs between messages
            self.msg = self.msg.lstrip('\x00')
            # parse the elements of the message              
            dlestx = self.msg[0:2]
            dest = self.msg[2:3]
            cmd = self.msg[3:4]
            args = self.msg[4:-3]
            if cmd.encode("hex") == "04": 
                ascii_args = " msg='" + filter(lambda x: x in string.printable, args) + "'"
            else: 
                ascii_args = ""
            checksum = self.msg[-3:-2]
            dleetx = self.msg[-2:]
            self.msg = ""
            debugMsg = "IN cmd="+cmd.encode("hex")+" args="+args.encode("hex")+ascii_args
            # stop reading if a message with a valid checksum is read
            if self.checksum(dlestx+dest+cmd+args) == checksum:
                if debugData:
                    if cmd.encode("hex") != "00" \
                    and cmd.encode("hex") != "01" \
                    and cmd.encode("hex") != "02" \
                    and dest.encode("hex") == "60": # only log coms from master and PDA
                        log(debugMsg)
                if args == None:
                    args = ""
                return {'dest':dest.encode("hex"), 'cmd':cmd.encode("hex"), 'args':args}
            else:
                log(debugMsg, "*** bad checksum ***")

    def sendMsg(self, (dest, cmd, args)):
        """ Send a message.
        The destination address, command, and arguments are specified as a tuple."""
        msg = DLE + STX + dest + cmd + args
        msg = msg + self.checksum(msg) + DLE + ETX
        
        if debugData:
            if args.encode("hex") != "4000": # don't log typical ACKs
                debugMsg = "OUT cmd="+cmd.encode("hex")+" args="+args.encode("hex")
                log(debugMsg)

        for i in range(2, len(msg) - 2):                       
            # if a byte in the message has the value \x10 insert a NUL after it
            if msg[i] == DLE:
                msg = msg[0:i+1]+NUL+msg[i+1:]
        n = self.port.write(msg)

    def checksum(self, msg):
        """ Compute the checksum of a string of bytes."""                
        return struct.pack("!B", reduce(lambda x, y:x+y, map(ord, msg)) % 256)


def main():
    """Start the listener for a screen, run webserver."""
    log("Creating screen emulator")
    screen = Screen()
    log("Creating RS485 port")
    i = Interface("RS485")
    log("Creating web server")
    server = threading.Thread(target=startServer, args=(screen,))
    server.start()

    while True:
        ret = i.readMsg()
        if 'stop' in ret:
            global webServer
            webServer.shutdown()
            return
        if ret['dest'] == ID:
            screen.processMessage(ret, i)


if __name__ == "__main__":
    main()
