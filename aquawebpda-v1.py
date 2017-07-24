#!/usr/bin/env python
# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
# coding=utf-8

"""
Simulates Aqualink PDA remote with a RS485 interface.

Huge thanks to efp3 and all the hard work he put into the original version of this script
that emulates the Aqualink RS and Spa control panels!

https://github.com/earlephilhower/aquaweb
"""

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

# Configuration

RS485Device = "/dev/ttyUSB0"        # RS485 serial device to be used
debugData = True				# logs to terminal and aw.log
debugRaw = False

# ASCII constants
NUL = '\x00'
DLE = '\x10'
STX = '\x02'
ETX = '\x03'

masterAddr = '\x00'          # address of Aqualink controller
ID = "60"                   # address of PDA remote to emulate

logging.basicConfig(filename='aw.log',filemode='a',format='%(message)s',level=logging.DEBUG)

INDEXHTML = """
<html>
<head>
<title>Pool Controller</title>
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
</head>
<body onload="screen();">
<table>
<tr>
<td>
<table><tr><td height="80px" align="right"><button onclick="sendkey('but1');">Button 1</button></td></tr><tr><td align="right" height="80px"><button onclick="sendkey('back');">Back</button></td></tr><tr><td align="right" height="80px"><button onclick="sendkey('but2');">Button 2</button></td></tr></table>
</td>
<td>
<font size="+2"><div id="screen"></div> </font>
</td>
<td>
    <table><tr><td align="left" height="80px"><button onclick="sendkey('up');">Up</button></td></tr><tr><td align="left" height="80px"><button onclick="sendkey('down');">Down</button></td></tr></table>
</td>
</tr>
<tr><td colspan="3" align="center"><button onclick="sendkey('select');">Select</button></td></tr>
</table>

</body>
</html>
"""

PORT = 80


class webHandler(BaseHTTPRequestHandler):
    """CGI and dummy web page handler to interface to control objects."""
    screen = None

    def log_request(self, code='-', size='-'):
        """Don't log anything, we're on an embedded system"""
        pass

    def log_error(self, fmt, *args):
        """This was an error, dump it."""
        self.log_message(fmt, *args)

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
           self.path.startswith("/spakey.cgi") or
           self.path.startswith("/spabinary.cgi") or
           self.path.startswith("/screen.cgi") or
           self.path.startswith("/spascreen.cgi") or
           self.path.startswith("/status.cgi") or
           self.path.startswith("/spastatus.cgi")):
            mimetype = 'text/html'
            ret = ""
            if self.path.startswith("/key.cgi"):
                if 'key' in postvars:
                    key = postvars['key'][0]
                    self.screen.sendKey(key)
                ret = "<html><head><title>key</title></head><body>"+key+"</body></html>\n"
            elif self.path.startswith("/spakey.cgi"):
                if 'key' in postvars:
                    key = postvars['key'][0]
                    self.spa.sendKey(key)
#                    print "SPA - key "+key
                    ret = "<html><head><title>key</title></head><body>"+key+"</body></html>\n"
            elif self.path.startswith("/spabinary.cgi"):
                ret = self.spa.text() + "|" + time.strftime("%_I:%M%P %_m/%d") + "|"
                if (self.spa.status['spa']=="ON"): ret += "1"
                else: ret += "0"
                if (self.spa.status['heat']=="ON"): ret += "1"
                else: ret += "0"
                if (self.spa.status['jets']=="ON"): ret += "1"
                else: ret += "0"
            elif self.path.startswith("/screen.cgi"):
                ret = self.screen.html()
            elif self.path.startswith("/spascreen.cgi"):
                ret = self.spa.html()
            elif self.path.startswith("/status.cgi"):
                ret = self.screen.status
            elif self.path.startswith("/spastatus.cgi"):
                ret = self.spa.status
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
        server = MyServer(('', PORT), webHandler)
        # Wait forever for incoming http requests
        server.serve_forever(screen)
    except KeyboardInterrupt:
        print '^C received, shutting down the web server'
        server.socket.close()


class Screen(object):
    """Emulates the Aqualink PDA remote control unit."""
    W = 16
    H = 10
    UNDERLINE = '\033[4m'
    END = '\033[0m'
    lock = None
    nextAck = "00"

    def __init__(self):
        """Set up the instance"""
        self.dirty = 1
        self.screen = self.W * [self.H * " "]
        self.invert = {'line':-1, 'start':-1, 'end':-1}
        self.status = "00000000"
        self.lock = threading.Lock()

    def setStatus(self, status):
        """Stuff status into a variable, but not used presently."""
        self.status = status

    def cls(self):
        """Clear the screen."""
        self.lock.acquire()
        try:
            for i in range(0, 10):
                self.screen[i] = ""
            self.invert['line'] = -1
            self.dirty = 1
        finally:
            self.lock.release()

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
            self.dirty = 1
        finally:
            self.lock.release()

    def writeLine(self, line, text):
        """"Controller sent new line for screen."""
        self.lock.acquire()
        try:
            self.screen[line] = text + self.W*" "
            self.screen[line] = self.screen[line][:self.W]
            self.dirty = 1
        finally:
            self.lock.release()

    def invertLine(self, line):
        """Controller asked to invert entire line."""
        self.lock.acquire()
        try:
            self.invert['line'] = line
            self.invert['start'] = 0
            self.invert['end'] = self.W
            self.dirty = 1
        finally:
            self.lock.release()

    def invertChars(self, line, start, end):
        """Controller asked to invert chars on a line."""
        self.lock.acquire()
        try:
            self.invert['line'] = line
            self.invert['start'] = start
            self.invert['end'] = end
            self.dirty = 1
        finally:
            self.lock.release()

    def show(self):
        """Print the screen to stdout."""
        self.lock.acquire()
        try:
            if self.dirty:
                self.dirty = 0
                os.system("clear")
                for i in range(0, self.H):
                    if self.invert['line'] == i:
                        sys.stdout.write(self.UNDERLINE)
                    sys.stdout.write(self.screen[i])
                    sys.stdout.write(self.END)
                    sys.stdout.write("\n")
                sys.stdout.write(self.W*"-" + "\n")
                sys.stdout.write("STATUS: " + self.status + "\n")
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

    def sendAck(self, i):
        """Controller talked to us, send back our last keypress."""
        ackstr = "40" + self.nextAck # was 8b before, PDA seems to be 400# for keypresses (4001-4006)
        i.sendMsg( (chr(0), chr(1), ackstr.decode("hex")) )
        self.nextAck = "00"

    def setNextAck(self, nextAck):
        """Set the value we will send on the next ack, but don't send yet."""
        self.nextAck = nextAck

    def sendKey(self, key):
        """Send a key (text) on the next ack."""
        keyToAck = { 'up':"06", 'down':"05", 'back':"02", 'select':"04", 'but1':"01", 'but2':"03" }
        if key in keyToAck.keys():
            self.setNextAck(keyToAck[key])

    def processMessage(self, ret, i):
        """Process message from a controller, updating internal state."""
        if ret['cmd'] == "09":  # Clear Screen
            # What do the args mean?  Ignore for now
            if (ord(ret['args'][0:1])==0):
                self.cls()
            else:  # May be a partial clear?
                self.cls()
#                print "cls: "+ret['args'].encode("hex")
            self.sendAck(i)
        elif ret['cmd'] == "0f":  # Scroll Screen
            start = ord(ret['args'][:1])
            end = ord(ret['args'][1:2])
            direction = ord(ret['args'][2:3])
            self.scroll(start, end, direction)
            self.sendAck(i)
        elif ret['cmd'] == "04":  # Write a line
            line = ord(ret['args'][:1])
            if line == 64: line = 1  # time (hex=40)
            if line == 130: line = 2  # temp (hex=82)
            offset = 1
            text = ""
            while (ret['args'][offset:offset+1].encode("hex") != "00") and (offset < len(ret['args'])):
                text += ret['args'][offset:offset+1]
                offset = offset + 1
            self.writeLine(line, text)
            self.sendAck(i)
        elif ret['cmd'] == "05":  # Initial handshake?
            # ??? After initial turn on get this, rela box responds custom ack
#            i.sendMsg( (chr(0), chr(1), "0b00".decode("hex")) )
            self.sendAck(i)
        elif ret['cmd'] == "00":  # PROBE
            self.sendAck(i)
        elif ret['cmd'] == "02":  # Status?
            self.setStatus(ret['args'].encode("hex"))
            self.sendAck(i)
        elif ret['cmd'] == "08":  # Invert an entire line
            self.invertLine( ord(ret['args'][:1]) )
            self.sendAck(i)
        elif ret['cmd'] == "10":  # Invert just some chars on a line
            self.invertChars( ord(ret['args'][:1]), ord(ret['args'][1:2]), ord(ret['args'][2:3]) )
            self.sendAck(i)
        else:
            log("UNKNOWN MESSAGE: cmd=" + ret['cmd'] + " args=" + ret['args'].encode("hex"))
            self.sendAck(i)

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
        self.debugRawMsg = ""
        # skip bytes until synchronized with the start of a message
        while (self.msg[-1] != STX) or (self.msg[-2] != DLE):
            self.msg += self.port.read(1)
            if debugRaw:
                self.debugRaw(self.msg[-1])
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
            if debugRaw: 
                self.debugRaw(self.msg[-2])
                self.debugRaw(self.msg[-1])
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
                if debugRaw:
                    self.debugRaw(self.msg[-1])
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
                ascii_args = " (" + filter(lambda x: x in string.printable, args) + ")"
            else: 
                ascii_args = ""
            checksum = self.msg[-3:-2]
            dleetx = self.msg[-2:]
            self.msg = ""
            debugMsg = "IN dest="+dest.encode("hex")+" cmd="+cmd.encode("hex")+" args="+args.encode("hex")+ascii_args
            # stop reading if a message with a valid checksum is read
            if self.checksum(dlestx+dest+cmd+args) == checksum:
                if debugData:
                    if cmd.encode("hex") != "00" \
                    and cmd.encode("hex") != "01" \
                    and cmd.encode("hex") != "02" \
                    and (dest.encode("hex") == "00" or dest.encode("hex") == "60"): # only log coms from master and PDA 
                        log(debugMsg)
                if args == None:
                    args = ""
                return {'dest':dest.encode("hex"), 'cmd':cmd.encode("hex"), 'args':args}
            else:
                if debugData:
                    log(debugMsg, "*** bad checksum ***")

    def sendMsg(self, (dest, cmd, args)):
        """ Send a message.
        The destination address, command, and arguments are specified as a tuple."""
        msg = DLE + STX + dest + cmd + args
        msg = msg + self.checksum(msg) + DLE + ETX
        
        if debugData:
            if args.encode("hex") != "4000": # don't log typical ACKs
                debugMsg = "OUT dest="+dest.encode("hex")+" cmd="+\
                    cmd.encode("hex")+" args="+args.encode("hex")
                log(debugMsg)

        for i in range(2, len(msg) - 2):                       
            # if a byte in the message has the value \x10 insert a NUL after it
            if msg[i] == DLE:
                msg = msg[0:i+1]+NUL+msg[i+1:]
        n = self.port.write(msg)

    def checksum(self, msg):
        """ Compute the checksum of a string of bytes."""                
        return struct.pack("!B", reduce(lambda x, y:x+y, map(ord, msg)) % 256)

    def debugRaw(self, byte):
        """ Debug raw serial data."""
        self.debugRawMsg += byte
        if ((len(self.debugRawMsg) == 48) or (byte==ETX)):
            log(self.debugRawMsg.encode("hex"))
            self.debugRawMsg = ""


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
        if ret['dest'] == ID:
            screen.processMessage(ret, i)


if __name__ == "__main__":
    main()
