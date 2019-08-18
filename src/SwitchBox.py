"""
SwitchBox
Copyrght (c) 2019 Jiawei Chen

Release Notes:
1.0 "Canberra": First Public Release. 
        Substantially clean up code
        Re-work file loading behavior
        Tweak status lights logic (Having a trigger but not a fader is 
            no longer considered an error condition)
        Add more comments to document code better
        Add a help menu 
        Add error messages that are displayed when something's not right
        
        1.0.1: Fix startup and file handling issues on macOS

0.2b "Brisbane": Adjust save file characteristics, naming logic, and 
    added "About" dialog. Removed file menus for now
    
    0.21b: Tweak version dialog and focusing logic

0.1b "Adelaide": Initial Commit, Entering Beta Testing

    0.11b: Made pressed keys release after channel switch so keys don't 
            get stuck.
"""

from tkinter import *
import tkinter.ttk as ttk 
import tkinter
from tkinter import messagebox
from os import popen,path,getcwd,mkdir
import logging
from lxml import etree
import webbrowser
from platform import system #Finds out if is a Mac or not
import rtmidi #MIDI IO library

# Because macOS uses different key codes
isaMac = system() == 'Darwin'

# Color codes for UI elements. 
GRAY = '#777777'
RED = 'red'
GREEN = '#035211'
YELLOW = '#FFB300'
LIGHTGREEN = '#07D720'

# Location of user's home path
PATH_HOME = path.expanduser('~')

# Where SwitchBox stores its temporary files
if isaMac:
    PATH_SWITCHBOXFILES = PATH_HOME + '/Library/Application Support/SwitchBox'
else:
    PATH_SWITCHBOXFILES = PATH_HOME + '/SwitchBox'

if not path.isdir(PATH_SWITCHBOXFILES):
    mkdir(PATH_SWITCHBOXFILES)
    
# Location of persistent settings file
PATH_CURRENT_XML = PATH_SWITCHBOXFILES + '/current.xml'

# Location of user manual
PATH_MANUAL = 'assets/SwitchBoxManual.pdf'

# Version strings for fancy formatting
VER_MAJOR = '1'
VER_MINOR = '0'
VER_PATCH = '1'
VER_NAME = 'Canberra'
VER_STRING = '{0}.{1}.{2}'.format(VER_MAJOR, VER_MINOR, VER_PATCH)

# Columns per row
NUM_COLS = 9
# Column element type
COL_NORMAL,COL_PAD = 0,1

# Sets UI element layout padding
LAYOUT_PAD_X = 5
LAYOUT_PAD_Y = 5

# How often to check for new instruments, in milliseconds. 
# Default = 500. 
# Higher numbers = more responsive, but higher CPU usage. Try to keep
#     this number reasonable, please.
# Lower numbers = takes longer to auto-connect or look for instruments
#     but uses less CPU.
INTERVAL_CHECKNEW_MS = 500

# Sets verbosity of debug printouts. Set to logging.INFO to print 
# everything, set to logging.WARN if you're annoyed with log spam.
LOG_LEVEL = logging.WARN

# Name of log file. If you want to log to a file, create a blank file
# with this name in PATH_SWITCHBOXFILES
# e.g. ~/Library/Application Support/SwitchBox/ on macOS,
# ~/SwitchBox/ on other platforms
# By default, SwitchBox logs to stdout if the file doesn't exist.
LOG_FILE = PATH_SWITCHBOXFILES + '/switcheroo.log'

LOG_FORMAT = '%(asctime)s %(levelname)s in module %(module)s:%(lineno)d: %(message)s'

"""A container for ColumnElements and MIDI ports

Each instrument gets its own virtual MIDI port for SwitchBox's output.
This appears as a "row" in the interface, with "columns" of controls
for each voice/patch of that instrument.
"""
class RowElement(): 
    """Create a row element.
    
    container -- The Tk Frame element that contains this RowElement
    row -- This row's number (starting from zero)
    num_cols -- The number of ColumnElements this row will produce
    XMLElement -- The part of the XML file that defines this RowElement
    upper -- points to the App instance that created this RowElement
    name -- The user-provided name of this row (string)
    """
    def __init__(self, container, row, num_cols, XMLElement, upper, 
                 name=None):
        self.upper = upper
        self.XMLElement = XMLElement
        self.rowName = name
                
        # Since we number rows from 0 internally
        self.rowNumber = row+1
        
        # The top element of the RowElement
        self.container = ttk.Frame(container)
        # Holds instrument info and other goodies related to all columns
        self.leftside = ttk.Frame(self.container) 
        # Container for the channel handlers
        self.columns = ttk.Frame(self.container) 
        # Buttons for adding/deleting columns
        self.plusminus = ttk.Frame(self.container) 
        
        # Are we currently listening for a fader or trigger? Or neither?
        self.listeningFor = None 
        
        # Gives a generic name if the row name isn't specified
        if self.rowName is None or self.rowName.strip() == '':
            self.rowName = 'Row ' + str(self.rowNumber)

        # The name of the row that appears when it is minimized
        self.rowLabel = ttk.Label(self.leftside, text=self.rowName, 
                                  width=10) 
        
        # Just so we can remember its position without gridding it.
        self.rowLabel.grid_configure(column=0, row=0, sticky=W, 
                                     padx=LAYOUT_PAD_X) 
        # Now hide it so it doesn't actually show up    
        self.rowLabel.grid_remove()
        
        # Set up MIDI ports
        self.inport = rtmidi.MidiIn() 
        self.outport = rtmidi.MidiOut()
        self.inport.set_callback(self.onReceived, None)
        self.outport.open_virtual_port(self.rowName + ' (SwitchBox)')
            
        """Auto-saves the row name when user types in the entry box. 
       
        Also elides long text and re-names the virtual MIDI output.
        Serves as a validation function for Tk Entry boxes
        Except it doesn't really validate. It's just something that gets
        called when the Entry box is edited.
        
        Arguments:
        P -- A string representing the row name to save
        """
        def saveRowName(P): 
            # Text-elide the label if it's too long to be displayed
            self.rowLabel['text'] = ((P[:8] + '...' if len(P) > 8 else P) 
                                     if P.strip() != '' 
                                     else 'Row ' + str(self.rowNumber))
            
            # Now change the name in XML
            self.XMLElement.attrib['name'] = P
            self.upper.saveFile()
            logging.info('Saved Row Name')
            
            # Shut down the current port. This might confuse some 
            # synth programs if used while port is connected.
            try:
                if self.outport.is_port_open():
                    self.outport.close_port()
                del(self.outport)
                logging.info('Closing port, about to re-open new port name')
            except:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                exc_type = exc_type.__name__
                logging.warning(str(exc_tb.tb_lineno) + ':' + 
                             str(exc_type) + ': ' + str(exc_obj) + 
                             ": Coulnd't close virtual port")
                
            # Attempt to re-open the port with the new name
            try:
                self.outport = rtmidi.MidiOut()
                if P != None and P.strip() != '':
                    self.outport.open_virtual_port(P + ' (SwitchBox)')
                else: 
                    self.outport.open_virtual_port('Row ' + 
                                                   self.rowNumber + 
                                                   ' (SwitchBox)')
            except:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                exc_type = exc_type.__name__
                logging.warning(str(exc_tb.tb_lineno) + ':' + 
                             str(exc_type) + ': ' + str(exc_obj) + 
                             ": Coulnd't re-open virtual port")
            
            # We're not really doing any validation, just trying to 
            # capture key input to auto-save names, so anything goes.
            return True 
        
        # Function pointer of the above function for TKinter's 
        # validation command
        updateRowName = (container.register(saveRowName), '%P') 
        self.rowNameEntry = ttk.Entry(self.leftside, width=10, 
                                      validate='key', 
                                      validatecommand=updateRowName)
        
        # Pre-fill the text box with its name
        self.rowNameEntry.insert(0, self.rowName) 
        self.rowNameEntry.grid(column=0, row=0, sticky=W)
        
        # MIDI input port selector
        self.indevice_choice = StringVar()
        self.gui_indeviceLabel = ttk.Label(self.leftside, text='Device')
        self.gui_indeviceLabel.grid(column=0, row=1, sticky=W)
        self.gui_indevice = ttk.Combobox(self.leftside, 
                                         postcommand=self.updateInDevices, 
                                         name='indevice', width=10, 
                                         state='readonly', 
                                         textvariable=self.indevice_choice)
        
        # An "LED" indicator--basically a colorful square that shows
        # status
        self.gui_led = tkinter.Label(self.leftside, width=2, bg=GRAY)
        self.gui_led.grid(column=1, row=0, sticky=(E))
        
        # Gives us event handling when we select a MIDI device, 
        # opening the port directly.        
        self.gui_indevice.bind('<<ComboboxSelected>>', 
                               self.onComboBoxSelected) 
        self.gui_indevice.grid(column=0, row=2)
        
        # List to hold columns
        self.cols = [] 
        
        # List to hold the input devices we detect
        self.inports = []
        
        # Create column elements for this row
        for num in range(num_cols):
            self.cols.append(ColumnElement(self.columns, num + 1, 
                                           self.onButtonPress, 
                                           self.validateNumbers,
                                           type=COL_NORMAL))
        # And one more column for the pad channel
        self.cols.append(ColumnElement(self.columns, num_cols + 1, 
                                       self.onButtonPress, 
                                       self.validateNumbers, 
                                       type=COL_PAD))
        
        # Sets column counter so that we can keep track of which columns 
        # correspond to which channel.
        self.num_cols = len(self.cols)
        
        self.leftside.grid(column=0, row=0, sticky=(N,S))
        self.columns.grid(column=2, row=0)
        
        self.padchannel = None
                
        # Horizontal bar that separates rows
        self.topSeparator = ttk.Separator(container, orient=HORIZONTAL)
        self.topSeparator.grid(column=0, row=row*2+1, sticky=(E,W), 
                               pady=LAYOUT_PAD_Y, padx=LAYOUT_PAD_X, 
                               columnspan=4)
        self.container.grid(column=0, row=row*2+2, sticky=(W), 
                            padx=LAYOUT_PAD_X, pady=LAYOUT_PAD_Y)    
        
        self.errmsg = None # Error Message hints to display on top menu
        
        self.isListening = False # Is the current channel in Listen mode? 
        self.whichListen = None # Which column is listening?
        self.disableAll() # Initialize row by disabling all channels.

        # Read from this row's XML element and update to match
        for cols in XMLElement:
            attr = cols.attrib
            chan = attr.get('chan')
            trig = attr.get('t')
            fade = attr.get('f')
            pad = attr.get('pad')
            # The +1 is for the pad channel at the end
            if (chan is not None and chan.isdigit() and 
                int(chan) - 1 in range(self.num_cols)):
                thisChannel = self.cols[int(chan) - 1]
                if fade is not None and fade.isdigit():
                    thisChannel.fader = int(fade)
                if (trig is not None and trig.isdigit() and 
                    thisChannel.type != COL_PAD):
                    thisChannel.trigger = int(trig)
                if (thisChannel.type == COL_PAD and
                    pad is not None and pad.isdigit()):
                    self.padchannel = int(pad)
                    thisChannel.padchannel = int(pad)
                    thisChannel.gui_padchannel.insert(0, pad)
            
        savedDevice = XMLElement.attrib.get('dev')
        self.updateInDevices()
        self.indevice_choice.set(savedDevice)
        if savedDevice in self.inports:
            logging.info('Found saved device. Attempting to connect...')
            if self.openPort(self.gui_indevice.current()):
                self.enableAll()
            else:
                logging.warning("Couldn't load port from savefile!")
        else:
            logging.info("Can't find saved device!")
            
        self.deactivateAll()
        self.cols[0].isActive = True
        self.activeChannel = 0
        self.updateAll() # Refresh status lights
          
    """Button handler that ColumnElements will call
    
    This handler deals with channel bindings, i.e. the trigger or fader
    paired to a channel, whether it's learning a new binding, or
    deleting an existing binding. 
    
    When the delete button is pressed, the column itself handles the
    deletion, but this handler is still needed to propagate those changes
    to the save file, which is only accessible by the RowElement.
    
    Arguments:
    FaderOrTrigger -- Either 'F' for fader or 'T' for trigger
    channel -- Channel number that sent the button press
    whichButton -- The button that sent the event--either the 'L' or 'C'
                    button in the ColumnElement
    """
    def onButtonPress(self, FaderOrTrigger, channel, whichButton): 
        self.listeningFor = FaderOrTrigger
        logging.info('Button press received: ' + str(FaderOrTrigger) + 
                     ':' + str(channel))
        
        # Get the pointer to the ColumnElement sending the event
        whichOne = self.cols[channel-1] 
        # If the button event was to "listen" for a new MIDI command
        if whichButton['text'] == 'L': 
            self.resetListenFlags()  
            self.disableAll()
            whichButton['text'] = 'C' # Button now cancels listen.
            whichOne.isDisabled = False
            whichOne.listening = True
            self.isListening = True
            self.whichListen = whichOne                        
            
        # To cancel listening or delete channel binding
        elif whichButton['text'] == 'C' or whichButton['text'] == 'X':
            # These lines handle the "cancel" case, but it also 
            # happens to be that deleting a binding has a lot of the same
            # actions as cancel, except we delete stuff at the end.
            self.isListening = False
            whichOne.listening = False
            self.whichListen = None
            self.listeningFor = None
            self.resetListenFlags()  
            
            # Store a temporary config for channel that's getting
            # a binding removed
            XMLColumnElement = None 
            if whichButton['text'] == 'X':
                logging.info('Deleted...saving XML')
                """Find the XML element that points to the channel that
                requested a delete
                """
                for cols in self.XMLElement:
                    if ('chan' in cols.attrib and 
                        cols.attrib['chan'] == str(channel)):
                        XMLColumnElement = cols
                # If the column does not exist (i.e. XML element is empty)
                if XMLColumnElement is None:
                    # First create a new XML element for this column
                    XMLColumnElement = etree.SubElement(self.XMLElement, 
                                                        'ch')
                    
                # If the column still has its fader after deletion, we
                # know it didn't just delete the fader. So we keep that.
                # Otherwise, delete it from the save file.
                if whichOne.fader is not None:
                    XMLColumnElement.attrib['f'] = str(whichOne.fader)
                    logging.info('Keeping fader')
                else:
                    XMLColumnElement.attrib.pop('f', None)
                    logging.info('Deleting fader')
                
                # Same here. We deduce that since the trigger is still
                # there after deletion, it mustn't have been the trigger
                # that requested to be deleted.
                if whichOne.trigger is not None:
                    XMLColumnElement.attrib['t'] = str(whichOne.fader)
                    logging.info('Keeping trigger')
                else:
                    XMLColumnElement.attrib.pop('t', None)
                    logging.info('Deleting trigger')
                logging.info('Saving file...')
                self.upper.saveFile()
            self.enableAll() # Resume our usual SwitchBox behavior.
        self.updateAll() # Refresh status lights one more time
    
    """Add a column to the row. Pretty self-explanatory.
    unused as of now.
    """
    def addColumn(self):
        self.num_cols += 1
        self.cols.append(ColumnElement(self.columns, self.num_cols, 
                                       self.onButtonPress,
                                       self.validateNumbers,
                                       type=COL_NORMAL))
    
    """ Remove a column from the row. Also pretty self-explanatory.
    Also unused as of now.
    """
    def delColumn(self):
        if self.num_cols > 1:
            self.cols[-1].container.grid_forget()
            del(self.cols[-1])
            self.num_cols -=1
    
    """Write a single channel's configuration to the save file.
    
    Arguments:
    whichChannel -- A ColumnElement object for which to save info.
    """
    def saveChannel(self, whichChannel):
        # Saving settings for a channel
        try:
            logging.info('Saving channel...')
            # Searching through all XML elements to find the channel we 
            # want. If it exists, modify it in-place. Otherwise, create a 
            # new XML element.
            columnFound = None 
            for cols in self.XMLElement:
                if cols.get('chan') == str(whichChannel.channel):
                    columnFound = cols
            if columnFound is None:
                columnFound = etree.SubElement(self.XMLElement, 'ch')
                columnFound.attrib['chan'] = str(whichChannel.channel)
            if whichChannel.type == COL_NORMAL:
                columnFound.attrib['t'] = str(whichChannel.trigger)
            else:
                columnFound.attrib['pad'] = str(self.padchannel)
            columnFound.attrib['f'] = str(whichChannel.fader)
            self.upper.saveFile()
        except:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            exc_type = exc_type.__name__
            logging.warning(str(exc_tb.tb_lineno) + ':' + str(exc_type) + 
                         ': ' + str(exc_obj) + ": Coulnd't save channel")
    
    """MIDI message callback
    
    This is the function that gives SwitchBox its behavior. It handles
    all the MIDI signals being sent to SwitchBox from your instrument,
    decides which ones to re-route to a different channel, and which ones
    to let pass through.
    
    MIDI messages are encoded as a sequence of 1+ bytes. The
        first byte always consists of [message type][channel], where
        both fields are four-bit words (nibbles).
        
        Message Type 0b1011 represents a Continuous Controller (e.g.)
            buttons, knobs, faders, pretty much anything that isn't a
            key. It has two bytes that follow: the id number of the 
            button, knob, etc. and the value it's set to (0-127). For
            buttons, usually 127 = pressed, 0 = not pressed, though this
            varies by keyboard, and can even be inverted.
            SwitchBox looks for these, since CC events can be bound to
            trigger the activation of a channel, or continue to change
            a CC value bound to that channel even if it's not currently 
            active.
        
        Message Type 0b1000 and 0b1001 are key events, specifically
            Key Down and Key Up events. These get their channel numbers 
            changed to match the active channel for this instrument
            in SwitchBox. Unless it's a pad channel. Some keyboards
            have "pads" for playing samples, which are
            programmed to a different channel from the normal keys.
            SwitchBox can be set up to recognize this (by typing the 
            pad's channel number as set by the keyboard) and pass these
            key events without changing their channel.
    
    Arguments:
    args -- An array containing a MIDI event and (unused) custom data, 
    where a MIDI event is a tuple--the first element is the MIDI message, 
    the second element is a number representing the number of seconds
    elapsed since the message was received.
    """
    def onReceived(self, *args): 
        logging.info('Callback called, received: ' + str(args))
        signalIn = args[0][0]

        # Get channel in the second nibble of the first byte.
        channel = signalIn[0] - ((signalIn[0] >> 4) << 4) 
        
        # Separates out the parts we're keeping the same, namely
        # everything except the channel number. First element of the 
        # array is the message type, everything that follows is the 
        # un-altered remainder of the MIDI message after the first byte.
        data = [signalIn[0] >> 4] + signalIn[1:] 
        
        logging.info('First Nibble: ' + str(data[0]))
        logging.info('Channel: ' + str(channel))
            
        # Catches all signals if listening for a binding
        if self.isListening: 
            #Catches CC signal to use as fader or trigger.
            if data[0] == 0b1011: 
                # Reset button states
                if self.listeningFor == 'T':
                    self.whichListen.trigger = data[1]
                elif self.listeningFor == 'F':
                    self.whichListen.fader = data[1]
                    
                self.enableAll()
                self.whichListen.listening = False
                self.saveChannel(self.whichListen)
                # Let everything else know we've stopped listening.
                self.whichListen = None
                self.listeningFor = None 
                self.isListening = False
                self.updateAll()
                
        # Ignores all events on pad channel
        elif (channel + 1) == self.cols[-1].padchannel:
            logging.info('Pad channel, Skipping')
            
        # Actual re-routing happens down here.
        else:
            # If it's a CC message
            if data[0] == 0b1011:
                foundTrigger = None
                foundFader = None
                
                # Check if CC is bound to anything as a trigger or fader
                for channels in self.cols:
                    if channels.trigger == data[1]:
                        logging.info('Trigger Match!')
                        foundTrigger = channels.channel - 1
                    if channels.fader == data[1]:
                        logging.info('Fader Match!')
                        foundFader = channels.channel - 1
                
                # If it is bound as a trigger, activate that channel.
                if foundTrigger is not None:
                    self.deactivateAll()
                    # Sends an "All Notes Off" signal to clear out any 
                    # stuck notes. Since it's possible that this change
                    # can occur when notes are held down, the key-up
                    # events for those notes may be re-routed to another
                    # channel, resulting in those notes continuously
                    # playing. Thankfully this was discovered during a
                    # rehearsal.
                    self.outport.send_message([(data[0] << 4) + 
                                               self.activeChannel, 123,0]) 
                    self.cols[foundTrigger].isActive = True
                    self.updateAll()
                    logging.info('CC Triggered')
                    self.activeChannel = foundTrigger
                
                # Respond to fader binding for any channel. Will send 
                # that fader to its bound channel, regardless of which
                # channel is currently active.
                elif foundFader is not None: 
                    # Attempt to make the LED on the channel blink when 
                    # we receive a fader by quickly toggling the active
                    # state of this channel while we're handling the
                    # fader event.
                    previousState = self.cols[foundFader].isActive 
                    self.cols[foundFader].isActive = True
                    self.cols[foundFader].checkStatus()
                    data[0] = (data[0] << 4) + foundFader
                    logging.info('Volume Fader')
                    self.outport.send_message(data)
                    if (not previousState):
                        self.cols[foundFader].isActive = False
                    self.cols[foundFader].checkStatus()
                    
                # Other CC message not tied to a particular channel 
                # action. Gets rerouted to the current channel.
                else: 
                    logging.info('Non-Triggerable CC ' + str(data))
                    logging.info('Active Channel: ' + 
                                 str(self.activeChannel))
                    data[0] = (data[0] << 4) + self.activeChannel                    
                    self.outport.send_message(data)
                    
            # Keydown and keyup events that aren't pads get re-routed
            # to current channel
            elif data[0] == 0b1000 or data[0] == 0b1001: 
                logging.info('Keystroke')
                logging.info('pad: ' + str(self.padchannel))
                if (self.padchannel is not None and 
                    (channel + 1 == self.padchannel)):
                    logging.info('Pad channel. Ignoring.')
                    self.outport.send_message(signalIn)
                else:
                    data[0] = (data[0] << 4) + self.activeChannel
                    logging.info('data out: ' +  str(data))
                    logging.info('Key Rerouted to Ch: ' + 
                                 str(self.activeChannel + 1))
                    self.outport.send_message(data)
                    
            # Pass through all other messages, without modification.        
            else:
                logging.info('Other MIDI Message')
                self.outport.send_message(signalIn)
                
    """Scan for MIDI devices and update selector
    
    Also auto-reconnects if current device is dropped but returns later.
    Returns True if device list changed. Otherwise, do nothing and 
    return False.
    """    
    def updateInDevices(self):
        newPorts = self.inport.get_ports()
        # Only do stuff if the port list changes from previous.
        if newPorts != self.inports:
            self.inports = self.inport.get_ports()
            choicelist = self.inports #Actual choice names
            if self.gui_indevice['values'] != choicelist:       
                self.gui_indevice['values'] = choicelist
            # Try to re-connect
            if ('dev' in self.XMLElement.attrib and 
                self.XMLElement.attrib['dev'] in choicelist):
                if self.openPort(choicelist.index(self.XMLElement.attrib['dev'])):
                    logging.info("Everything's good here!")
                else:
                    logging.warning("Couldn't auto-reconnect to device!")
            else:
                logging.info('Row ' + str(self.rowNumber) + 
                             ' does not have a device saved.')
            return True
        else:
            return False
        
    """Do things that default all columns to not listening anymore 
    """   
    def resetListenFlags(self): 
        logging.info('Clearing Out Listen Flags!')
        self.isListening = False
        #Clears out all the channel listen flags
        for columns in self.cols: 
            columns.gui_faderlisten['text'] = 'L'
            if columns.type == COL_NORMAL:   
                columns.gui_triggerlisten['text'] = 'L'
            columns.listening = False
    
    """Disable all columns in the row (pretty self-explanatory)
    """
    def disableAll(self):
        logging.info('Disabling all in row')
        for columns in self.cols:
            columns.isDisabled = True

    """Likewise, enable all columns in the row
    """
    def enableAll(self):
        logging.info('Enabling all in row')
        for columns in self.cols:
            columns.isDisabled = False
    
    """Update status of this row, including columns.
    
    The row is "green" if an instrument is connected. Otherwise, it's
    "red".
    """
    def updateAll(self):
        self.errmsg = None
        
        if self.indevice_choice.get() in self.inports:
            self.gui_led['bg'] = LIGHTGREEN
            if not self.isListening:
                self.resetListenFlags()
                self.enableAll()
        else:
            self.gui_led['bg'] = RED
            self.resetListenFlags()
            self.disableAll()
            self.errmsg = 'Not connected to MIDI Device'
        
        for columns in self.cols:
            columns.checkStatus()
            if self.errmsg is None and columns.errmsg is not None:
                self.errmsg = (('CH ' + str(columns.channel) + ' ') if columns.type == COL_NORMAL
                                else 'PAD ') + columns.errmsg
                                
        self.upper.updateErrorMessage()
                
    """Different from disabling: just make none of the columns active.
    """
    def deactivateAll(self):
        for columns in self.cols:
            columns.isActive = False    
    
    """Close old port and attach to a new port.
    
    Returns True on success, False on failure.
    """
    def openPort(self, portIndex):
        try:
            logging.info('Clearing out old port')
            self.inport.close_port()
            self.inport.open_port(portIndex)
            self.inport.set_callback(self.onReceived, None)
            logging.info('New Port opened!')
            logging.info('Port' + str(portIndex))
            logging.info('Port Name: ' + self.inports[portIndex])
            return True
        except:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            exc_type = exc_type.__name__
            logging.warning(str(exc_tb.tb_lineno) + ':' + str(exc_type) + 
                         ': ' + str(exc_obj) + ": Coulnd't open port")

            self.indevice_choice.set('')
            self.updateAll()
            return False
        
    """Event handler for Tk combo box.
    
    We're using it to catch when the user changes ports.
    """    
    def onComboBoxSelected(self, event):
        logging.info('Combobox selected')
        whichOne = event.widget.bindtags()[0]
        if 'indevice' in whichOne: 
            # Opens port in port list with this index.
            if self.openPort(self.gui_indevice.current()): 
                try:   
                    # Try to save new device       
                    self.XMLElement.attrib['dev'] = self.indevice_choice.get()
                    self.upper.saveFile()                    
                except:
                    exc_type, exc_obj, exc_tb = sys.exc_info()
                    exc_type = exc_type.__name__
                    logging.warning(str(exc_tb.tb_lineno) + ':' + 
                                 str(exc_type) + ': ' + str(exc_obj) + 
                                 ": Coulnd't save selected port")
                self.resetListenFlags()
                self.enableAll()
            else:
                logging.warning("Can't select that port")
            self.updateAll()
    
    """Not really a validation, but merely an event handler.
    
    Gets called when the pad channel entry box gets modified,
    so changes are applied and saved immediately.
    """
    def validateNumbers(self, whoCalled, channel):
        logging.info('Validated Number')
        self.padchannel = channel
        self.updateAll()
        self.saveChannel(whoCalled)
        
    """Makes this row normal-sized
    """
    def maximize(self):
        self.rowNameEntry.grid()
        self.rowLabel.grid_remove()
        self.gui_indeviceLabel.grid()
        self.gui_indevice.grid()
        for cols in self.cols:
            cols.maximize()
            
    """Shrinks the row by hiding all non-essential controls
    """
    def minimize(self):
        self.rowNameEntry.grid_remove()
        self.rowLabel.grid()        
        self.gui_indeviceLabel.grid_remove()
        self.gui_indevice.grid_remove()
        for cols in self.cols:
            cols.minimize()
            
            
"""ColumnElement: A container for the settings of an individual channel.

Each ColumnElement represents a different "voice" or "patch" that the 
instrument can switch to. A ColumnElement can be "active", "inactive",
"disabled", or "listening."

In the Active state, a ColumnElement has been triggered, and MIDI
messages from its associated instrument are being routed through its
channel. 

In the Inactive state, a ColumnElement has not yet been triggered, but
its associated fader, if there is one, will continue to control the 
volume of the instrument's channel. 

In the Disabled state, a ColumnElement cannot be triggered, and will 
not respond to any fader messages. This only occurs when the 
ColumnElement is not correctly configured, or another ColumnElement 
in its row is listening for a binding.

Speaking of, in the Listening state, a ColumnElement is waiting for an 
incoming MIDI CC message to bind to either its trigger or fader.

A "Pad" channel is a special variant of ColumnElement, in which
there is no trigger--it is always in the Active state. However, it only 
re-routes MIDI messages that have a channel number matching that of 
the channel number given in the "PAD" setting. 

The logic for all this is primarily handled in the RowElement; this is 
merely a container that holds state values and settings.
"""
class ColumnElement():
    """Initializes a ColumnElement
    
    Arguments:
    container -- Points to the Tk frame that holds this ColumnElement.
    channel -- A number starting from 1 that indicates the channel this
        ColumnElement will re-route MIDI signals to.
    callback -- Points to a callback function in the RowElement that 
        handles button presses this ColumnElement generates.
    validateCommandUpper -- Points to a callback in the RowElement that
        immediately saves the XML file once an entry box is edited.
    type -- A type of COL_NORMAL (0) creates a normal column. A type of
        COL_PAD (1) creates a column that handles pad keystrokes. All 
        other values are undefined.
    """
    def __init__(self, container, channel, callback, 
                 validateCommandUpper, type=0):
        """Checks that the user enters a valid number for a pad channel.
        
        A valid entry is a number in the range 0-99, or blank. Once
        validated, the number is either updated or deleted in the XML
        file.
        """
        def validateCommand(S, P):
            # Let's not get too extreme with our channel numbering!
            if P != '' and S.isdigit() and int(P) <= 99: 
                self.padchannel = int(P)
                validateCommandUpper(self, int(P))
                logging.info('Updated pad channel')
                return True
            elif P == '':
                self.padchannel = None
                validateCommandUpper(self, None)
                logging.info('Cleared pad channel')
                return True
            else:
                return False
                
        # Validation for entry boxes requiring only numbers
        updatePadChannel = (container.register(validateCommand),
               '%S', '%P') 
        self.callback = callback
    
        self.channel = channel
        self.fader = None
        self.trigger = None
        self.padchannel = None

        self.listening = False #Checks if specific fader is listening
        self.isActive = False #Is this the current patch?
        # Has this channel been disabled? 
        # Usually should only happen if another channel is 
        # listening for a new trigger/fader
        self.isDisabled = False 
        
        self.errmsg = None #Returns message if something goes wrong
        
        # Various Tk frames to get the UI element placements right.
        self.container = ttk.Frame(container)
        self.bottomside = ttk.Frame(self.container)
        self.rightside = ttk.Frame(self.bottomside)
        self.faderbuttons = ttk.Frame(self.rightside)
        self.triggerbuttons = ttk.Frame(self.rightside)
        
        self.type = type
        
        # Channel label
        if self.type == COL_NORMAL:
            self.gui_channellabel = ttk.Label(self.rightside, 
                                              text='CH ' + str(channel), 
                                              justify=LEFT)
        else:
            self.gui_channellabel = ttk.Label(self.rightside, 
                                              text='PAD', justify=LEFT)
    
        self.gui_channellabel.grid(column=0, row=0, sticky=(W))
        
        # Status "LED" indicator. 
        # Grey=Not Configured/Disabled
        # Red=Error
        # Dark Green=Inactive
        # Light Green=Active
        # Yellow=Listening
        self.gui_led = tkinter.Label(self.rightside, width=2, bg='grey')
        self.gui_led.grid(column=1, row=0, sticky=(E))
        
        # Fader label (Looks like: "F: CC128")
        self.gui_faderlabel = ttk.Label(self.rightside, text='F:')
        self.gui_faderlabel.grid(column=0, row=1, sticky=(E))
        self.gui_fadervalue = ttk.Label(self.rightside, text='N/A', width=6)
        self.gui_fadervalue.grid(column=1, row=1, sticky=(W))
        
        # Fader listen button
        # This is kinda crazy here. We want to handle these buttons 
        # with the RowElement. This makes saving/disabling/re-enabling 
        # all the columns at once much easier This gives our class 
        # access to the main loop to hit the upper callback function 
        # for all button events. Lambda is used here to pass args. to 
        # our callback. Sorry these lines break PEP-8 line length rules.
        self.gui_faderlisten = ttk.Button(self.faderbuttons, 
                                          text='L', width=1, 
                                          command=lambda:callback('F', channel, self.gui_faderlisten)) 
        
        # This button clears any fader bindings
        self.gui_faderclear = ttk.Button(self.faderbuttons, text='X', 
                                         width=1, 
                                         command=self.deleteFader) 
        self.gui_faderlisten.grid(column=0, row=0)
        self.gui_faderclear.grid(column=1, row=0)
        self.faderbuttons.grid(column=0, row=2, columnspan=2)
        
        self.gui_padchannel = ttk.Entry(self.rightside, width=3, 
                                        validate='key', 
                                        validatecommand=updatePadChannel)
        
        # Trigger listen buttons / labels
        # If this channel isn't a pad channel...
        if self.type == COL_NORMAL: 
            # Trigger Label (Looks like: "T: CC48")
            self.gui_triggerlabel = ttk.Label(self.rightside, text='T:')
            self.gui_triggerlabel.grid(column=0, row=3, sticky=(E))
            self.gui_triggervalue = ttk.Label(self.rightside, 
                                              text='N/A', width=6)
            self.gui_triggervalue.grid(column=1, row=3, sticky=(W))
            self.gui_triggerlisten = ttk.Button(self.triggerbuttons, 
                                                text='L', width=1, 
                                                command=lambda:callback('T', channel, self.gui_triggerlisten))  # Haha, passing the button itself as an argument! How meta!
            self.gui_triggerclear = ttk.Button(self.triggerbuttons, 
                                               text='X', width=1, 
                                               command=self.deleteTrigger) 
            self.gui_triggerlisten.grid(column=0, row=0)
            self.gui_triggerclear.grid(column=1, row=0)
            self.triggerbuttons.grid(column=0, row=4, columnspan=2)
            
        # If it's a pad channel, we don't need to trigger it, so those
        # UI elements aren't displayed.
        else:
            # Literally just the word "Channel" above the entry box
            self.gui_channelBoxHint = ttk.Label(self.rightside, 
                                                text='Channel', 
                                                justify=CENTER)
            self.gui_channelBoxHint.grid(column=0, row=3, columnspan=2)
            self.gui_padchannel.grid(column=0, row=4, columnspan=2)
        
        
        self.rightside.grid(column=1, row=1, sticky=(E))
        # Separator between columns
        ttk.Separator(self.bottomside, orient=VERTICAL).grid(column=0, 
                                                             row=1, 
                                                             sticky=(N,S), 
                                                             padx=LAYOUT_PAD_X)
        
        self.bottomside.pack()
        self.container.grid(row=0, column=(channel-1))
        
        # This is a list of all the elements that could be hidden when 
        # SwitchBox goes into "minimized" mode.
        self.minimizeableElements = [self.faderbuttons, 
                                     self.triggerbuttons, 
                                     self.gui_faderlabel, 
                                     self.gui_fadervalue]
        if self.type == COL_NORMAL:
            self.minimizeableElements.extend([self.gui_triggerlabel, 
                                              self.gui_triggervalue])
        else:
            self.minimizeableElements.extend([self.gui_padchannel, 
                                              self.gui_channelBoxHint])
        # Give an update before we finish initializing.
        self.checkStatus()
    
    """Updates the status and reports back any errors.
    
    Basically makes the blinkenlights show the right colors, and 
    updates the labels
    """
    def checkStatus(self):
        self.errmsg = None
        if not self.listening:
            self.gui_faderlisten['text'] = 'L'
            if self.type == COL_NORMAL:
                self.gui_triggerlisten['text'] = 'L'
                
        if self.listening:
            self.gui_led['bg'] = YELLOW
            self.errmsg = 'is listening'
            logging.info('Is Listening')
            
        # If a channel has no fader and no trigger, that's fine. It
        # just won't do anything.
        elif self.fader is None and self.trigger is None:
            self.gui_led['bg'] = GRAY
            
        elif self.isDisabled:
            self.gui_led['bg'] = GRAY
            
        elif (self.type == COL_PAD and 
              self.padchannel is None and 
              self.fader is None):
            self.gui_led['bg'] = GRAY
            
        # A channel has to have a trigger. If there's also a fader
        # paired, it can't be re-routed, which is a problem.
        elif self.trigger is None and self.type == COL_NORMAL:
            self.errmsg = 'has a Fader, but no Trigger'
            self.gui_led['bg'] = RED
        
        # A pad channel only works when it has a channel number assigned
        # Otherwise, it wouldn't make sense to have a fader paired
        # since it would never be re-routed.
        elif (self.type == COL_PAD and 
              self.padchannel is None and 
              self.fader is not None):
            self.errmsg = 'has a Fader, but no Pad Channel.'
            self.gui_led['bg'] = RED
        
        # Because pad channels are always active.
        elif self.type == COL_PAD:
            self.gui_led['bg'] = LIGHTGREEN
            
        elif self.isActive:
            self.gui_led['bg'] = LIGHTGREEN
        
        else:
            self.gui_led['bg'] = GREEN
        
        # This grays out widgets if current channel is disabled.
        if self.isDisabled: 
            self.gui_faderclear['state'] = DISABLED
            self.gui_faderlisten['state'] = DISABLED
            if self.type == COL_NORMAL:
                self.gui_triggerclear['state'] = DISABLED
                self.gui_triggerlisten['state'] = DISABLED
            else:
                self.gui_padchannel['state'] = DISABLED
        else:
            self.gui_faderclear['state'] = NORMAL
            self.gui_faderlisten['state'] = NORMAL
            if self.type == COL_NORMAL:
                self.gui_triggerclear['state'] = NORMAL
                self.gui_triggerlisten['state'] = NORMAL
            else:
                self.gui_padchannel['state'] = NORMAL
        
        if self.type == COL_NORMAL:
            if self.trigger is None:
                self.gui_triggervalue['text'] = 'N/A'
            else:
                self.gui_triggervalue['text'] = 'CC' + str(self.trigger)
            
        if self.fader is None:
            self.gui_fadervalue['text'] = 'N/A'
        else:
            self.gui_fadervalue['text'] = 'CC' + str(self.fader) 
            
    """ Button handler for when the "delete" button is pressed on a 
    trigger binding.
    """
    def deleteTrigger(self):
        self.trigger = None
        self.listening = False
        self.checkStatus()
        self.callback('T', self.channel, self.gui_triggerclear)
        
    """Same thing but for faders.
    """
    def deleteFader(self):
        self.fader = None
        self.listening = False
        self.checkStatus()
        self.callback('F', self.channel, self.gui_faderclear)
        
    """Puts this ColumnElement into a "minimized" state.
    
    This makes the column more compact by only showing essential info.
    """
    def minimize(self):
        for elements in self.minimizeableElements:
            elements.grid_remove()
            
    """The opposite of the above function.
    """
    def maximize(self):
        for elements in self.minimizeableElements:
            elements.grid()
        

"""The App class is basically a Tkinter frame that runs the whole show.
It contains all the RowElements and handles "top-level" operations,
like scanning MIDI ports, keyboard shortcuts, and XML read/write 
operations.
"""
class App(ttk.Frame):
    """Initialize the App
    
    Arguments:
    master -- A Tk Frame that holds the App. Most likely a top-level
        window.
    """
    def __init__(self, master):
        ttk.Frame.__init__(self, master)
        
        self.menu = Menu(self.master)      
        
        helpmenu = Menu(self.menu)
        helpmenu.add_command(label='SwitchBox Help',
                            command=self.help, accelerator='F1')
        self.bind_all('<F1>', self.help)
        
        # If on a Mac, make the "About" menu show up in the menu with 
        # the application's name in it (Apple menu). Otherwise, make it 
        # show up in the "Help" menu.
        if isaMac:
            applemenu = Menu(self.menu, name='apple') 
            applemenu.add_command(label='About SwitchBox', 
                                  command=self.on_about_action)
            self.menu.add_cascade(menu=applemenu)
        else:
            helpmenu.add_command(label='About SwitchBox', 
                                  command=self.on_about_action)
            
        self.menu.add_cascade(menu=helpmenu, label='Help')
        master.config(menu=self.menu) 
        
        # Sets up keyboard shortcuts 
        if isaMac: 
            master.bind('<Mod1-w>', self.onApplicationClose)
        else:
            master.bind('<Control-q>', self.onApplicationClose)
            master.bind('<Control-w>', self.onApplicationClose)
        
        # Set up window close event handler
        master.protocol('WM_DELETE_WINDOW', self.onApplicationClose)
        
        self.rowlist = []
        # Holds the RowElements and "top bar"
        self.windowUpper = ttk.Frame(self.master)
        # Holds the horizontal separator and +/- buttons
        self.windowLower = ttk.Frame(self.master)
        # Holds maximize/minimize button and status text
        self.topbar = ttk.Frame(self.windowUpper)
        # Holds +/- buttons
        self.bottombar = ttk.Frame(self.windowLower)
        
        # Maximize/Minimize button
        self.isExpanded = True
        self.expand = ttk.Button(self.topbar, text='Minimize', 
                                 command=self.on_expand_pressed)
        self.expand.grid(column=0, row=0, padx=LAYOUT_PAD_X, 
                         pady=LAYOUT_PAD_Y)
        
        # Status text on topbar
        self.gui_errmsg = ttk.Label(self.topbar)
        self.gui_errmsg.grid(column=1, row=0, padx=LAYOUT_PAD_X, 
                             pady=LAYOUT_PAD_Y)
        
        self.topbar.grid(column=0, row=0, sticky=(W,E))
        
        # Buttons for adding/removing a slot
        self.gui_add = ttk.Button(self.bottombar, text='+', width=1, 
                                  command=self.addRow)
        self.gui_sub = ttk.Button(self.bottombar, text='-', width=1, 
                                  command=self.delRow)
        self.gui_add.grid(column=1, row=0, padx=LAYOUT_PAD_X, 
                          pady=LAYOUT_PAD_Y)
        self.gui_sub.grid(column=2, row=0, pady=LAYOUT_PAD_Y)
        
        # Bottom separator to divide RowElements from +/- buttons
        ttk.Separator(self.windowLower, 
                      orient=HORIZONTAL).pack(fill='x', 
                                              padx=LAYOUT_PAD_X)
        self.bottombar.pack(fill='x')
        
        self.readState()
        
        # Prevents you from deleting rows when there's only one left.
        if len(self.rowlist) <= 1:
            self.gui_sub['state'] = DISABLED 
            
        self.windowUpper.pack()
        self.windowLower.pack(fill='x')
                        
        self.myXML = self.myTree.getroot()
        
        logging.info('About to enter loop!')
        master.after(INTERVAL_CHECKNEW_MS, self.onUpdateTick)
    
    """Load savefile from XML file
    """    
    def readState(self):
        # Try to open file; Creates a brand new one if it can't
        try:
            self.myTree = etree.ElementTree(file=PATH_CURRENT_XML)
            logging.info('Successfully read XML')
        except:
            logging.warning('Cannot read savefile; Creating new file')
            myRoot = etree.Element('swr')
            myRoot.set('title', 'Auto-Generated Save File')
            etree.SubElement(myRoot, 'row')
            self.myTree = etree.ElementTree(element=myRoot)
            self.myTree.write(PATH_CURRENT_XML, pretty_print=True)
        
        # This is where the XML loading magic happens
        for rows in self.myTree.getroot():
            logging.info('Reading row from XML...')
            # Pass the XML element to the row to have it configure itself
            newRow = RowElement(self.windowUpper, 
                                self.myTree.getroot().index(rows), 
                                NUM_COLS, 
                                rows, 
                                self, 
                                name=rows.attrib.get('name'))
            if not self.isExpanded:
                newRow.minimize()
            self.rowlist.append(newRow)
            
        minimizeAttrib = self.myTree.getroot().attrib.get('min')
        if minimizeAttrib == 't':
            self.setExpand(False)
        else:
            self.setExpand(True)
        self.updateErrorMessage()
       
    """Print entire XML
    """ 
    def printXML(self):
        logging.info(etree.tostring(self.myTree, pretty_print=True))   
    
    """Write XML to file
    """
    def saveFile(self):
        logging.info('Saving XML...')
        try:
            self.myTree.write(PATH_CURRENT_XML, pretty_print=True)
        except:
            exc_type, exc_obj, exc_tb = sys.exc_info()
            exc_type = exc_type.__name__
            logging.warning(str(exc_tb.tb_lineno) + ':' + str(exc_type) + 
                         ': ' + str(exc_obj) + ": Coulnd't save file")
        
    """Sets whether window is maximized or not.
    
    Arguments:
    expand -- Boolean, True for maximized, False for minimized.
    """
    def setExpand(self, expand):
        self.isExpanded = expand
        if expand:
            logging.info('Maximizing')
            self.expand['text'] = 'Minimize'
            self.myTree.getroot().attrib['min'] = 'f'
            self.bottombar.pack(fill='x') # Unhide bottom bar
            for rows in self.rowlist: # Expand all row elements
                rows.maximize()   
        else:
            logging.info('Minimizing')
            self.expand['text'] = 'Maximize'
            self.myTree.getroot().attrib['min'] = 't'
            self.bottombar.pack_forget() # Hide bottom bar from view
            for rows in self.rowlist: # Shrink all row elements
                rows.minimize()
        
    """Toggle whether or not the window is "Expanded" out.
    
    Also saves that state in persistent settings.
    """
    def on_expand_pressed(self):
        self.setExpand(not self.isExpanded)
        self.saveFile()
        
    """Create and show the "About" screen
    """
    def on_about_action(self):
        about_window = Toplevel(self.master)
        
        """Close handler for the about menu
        
        We're not using the args.
        """
        def on_about_close(*args): 
            about_window.grab_release()
            about_window.destroy()
            
        # Set about window window close handler
        about_window.protocol('WM_DELETE_WINDOW', on_about_close) 
            
        about_window.title('About SwitchBox')
        about_frame = ttk.Frame(about_window)
        about_frame.pack(fill=BOTH, expand=True)
        
        # Yeah nah, this'll be a mess to trim as per PEP-8 standards.
        logo_image = PhotoImage(file='assets/SwitchBox.gif', format='gif')
        ttk.Label(about_frame, image=logo_image).pack(pady=LAYOUT_PAD_Y, padx=64)
        ttk.Label(about_frame, text='SwitchBox {}'.format(VER_STRING), font='-weight bold -size 20').pack(padx=LAYOUT_PAD_X)
        ttk.Label(about_frame, text='"{}"'.format(VER_NAME)).pack(padx=LAYOUT_PAD_X*4)
        ttk.Label(about_frame, text='Copyright (c) 2019 Jiawei Chen').pack(padx=LAYOUT_PAD_X)
        ttk.Label(about_frame, text='SwitchBox is Open-Source Software').pack(padx=LAYOUT_PAD_X)
        ttk.Button(about_frame, text='Github', command=lambda : webbrowser.open_new('https://www.github.com/SomeInterestingUserName/SwitchBox')).pack(padx=LAYOUT_PAD_X)
        ttk.Label(about_frame, text='Python {0}.{1}.{2}'.format(*sys.version_info[:3])).pack(padx=LAYOUT_PAD_X*4)
        ttk.Label(about_frame, text='Using Tcl/Tk {}'.format(tkinter.Tcl().eval('info patchlevel'))).pack(padx=LAYOUT_PAD_X*4)
        ttk.Label(about_frame, text='Powered by rtmidi {0}'.format(rtmidi.get_rtmidi_version())).pack(padx=LAYOUT_PAD_X*4)
        
        ttk.Button(about_frame, text='Close', command=on_about_close).pack(pady=LAYOUT_PAD_Y*4, padx=LAYOUT_PAD_X)
        
        # Binds the "close window" key combination depending on OS
        if isaMac:
            about_window.bind('<Mod1-w>', on_about_close)
        else:
            about_window.bind('<Control-w>', on_about_close)
        about_window.bind('<Return>', on_about_close)
        
        about_window.resizable(False, False)
        about_window.transient(self.master)
        about_window.grab_set()
        about_window.focus()
        # This is required because otherwise, the window won't show the
        # image (Window manager weirdness?)
        self.master.wait_window(about_window)
        
    """Called when the SwitchBox get closed
    
    We're not using any of the arguments that Tk passes us.
    """
    def onApplicationClose(self, *args):
        self.master.destroy() #Event logic to quit program
    
    """Add a new blank row
    """
    def addRow(self):
        channelnumber = len(self.myXML)
        newElement = etree.SubElement(self.myXML, 'row')
        self.rowlist.append(RowElement(self.windowUpper, 
                                       channelnumber, 
                                       NUM_COLS, 
                                       newElement, 
                                       self))
        self.saveFile()
        self.gui_sub['state'] = NORMAL
    
    """Get rid of a row, but ask nicely
    """
    def delRow(self):
        if messagebox.askokcancel('', 'Are you sure you want to delete a row? This cannot be undone.'):
            toDelete = self.rowlist[-1]
            toDelete.container.grid_forget()
            toDelete.topSeparator.grid_forget()
            self.myXML.remove(toDelete.XMLElement)
            del(self.rowlist[-1])
            self.printXML()
            self.saveFile()
            if len(self.rowlist) < 2:
                self.gui_sub['state'] = DISABLED
        else:
            pass
        
    """Repeatedly checks for new MIDI devices, but not too often
    """
    def onUpdateTick(self):
        for rows in self.rowlist:
            if rows.updateInDevices():
                rows.updateAll()
                self.updateErrorMessage()
        self.master.after(INTERVAL_CHECKNEW_MS, self.onUpdateTick)

    """Opens a PDF help document
    
    We don't care about the args.
    """
    def help(self, *args):
        webbrowser.open_new('file://' + getcwd() + 
                            '/' + PATH_MANUAL)
            
    """Updates error message by checking all RowElements
    """
    def updateErrorMessage(self):
        errmsg = None
        for rows in self.rowlist:
            if errmsg is None and rows.errmsg is not None:
                errmsg = rows.rowName + ': ' + rows.errmsg
        if errmsg is not None:
            self.gui_errmsg['text'] = errmsg
        else:
            self.gui_errmsg['text'] = ''
            
            
"""As per the definition name, this is the main routine.
"""
def main():
    # Set up logging.
    # If the log file exists, log to that. Otherwise, print to stdout to 
    # prevent logfile spam
    if path.isfile(LOG_FILE) :
        logging.basicConfig(filename=LOG_FILE, level=LOG_LEVEL, 
                            format=LOG_FORMAT)
    else:
        logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
        logging.warning('Logfile not found, logging to console instead. If you would like to log to a file, create a file named "{}" in the working directory.'.format(LOG_FILE))
        
    root = Tk()
    root.resizable(False, False)
    app = App(root)
    
    logging.info("G'day!")
    app.master.title('SwitchBox')
    root.focus()
    root.mainloop()

if __name__ == '__main__':
    main()