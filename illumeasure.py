#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Illuminance measurement with T-10A illuminance meter.
"""

import os
import sys
import ftdi1 as ftdi
import time
from functools import reduce



class FtdiContext:

  vendorID = 0x0403
  productID = 0x6001
  
  class FtdiContextException(Exception): pass

  def __assertFtdi(self, name, ret):
    if ret < 0:
      raise self.FtdiContextException("ftdi.%s failed: %d (%s)" % (name, ret, ftdi.get_error_string(self.ftdi)))

  def __init__(self):
    self.ftdi = ftdi.new()
    if self.ftdi == 0:
      raise Exception('ftdi.new failed: %d' % ftdi.get_error_string(self.ftdi))
    self.__assertFtdi("usb_open", ftdi.usb_open(self.ftdi, self.vendorID, self.productID))
    self.__assertFtdi("setflowctrl", ftdi.setflowctrl(self.ftdi, ftdi.SIO_XON_XOFF_HS))
    self.__assertFtdi("set_bitmode", ftdi.set_bitmode(self.ftdi, 0xff, ftdi.BITMODE_RESET))
    self.__assertFtdi("set_baudrate", ftdi.set_baudrate(self.ftdi, 9600))
    self.__assertFtdi("set_line_property", ftdi.set_line_property(self.ftdi, ftdi.BITS_7, ftdi.STOP_BIT_1, ftdi.EVEN))      

  def writeData(self, data:str):
    '''
    Writes data to device using the ftdi library. Returns the response from the device
    
    :param data: String containing the data formatted as bytes
    :type data: str
    '''
    ret = ftdi.write_data(self.ftdi, data)
    self.__assertFtdi("write_data", ret)
    return ret

  def readData(self, length:int):
    '''
    Reads data from the device using the ftdi library. Returns the data in bytes
    
    :param length: Length of the data as an integer
    :type length: int
    '''
    ret, d = ftdi.read_data(self.ftdi, length)
    self.__assertFtdi("read_data", ret)
    return d
  
  def endConnection(self):
    '''
    Closes the connection to the device and cleans up.
    '''
    self.__assertFtdi("usb_close", ftdi.usb_close(self.ftdi))
    ftdi.free(self.ftdi)
    print("Device closed")



# IlluminanceMeterT10A
class Messenger:

  __messageLengthShort = 14
  __messageLengthLong = 32

  class BCCException(Exception): pass
  class PowerOffError(Exception): pass
  class EEPROMError(Exception): pass
  class LowBatteryError(Exception): pass #TODO: consider discarding most recent measurement when this error is raised
  
  def computeBcc(i:str):
    '''
    Computes result for the BCC Check by XORing the message bytes from start to end
    
    :param i: input bytes
    :type i: str
    '''
    bytes = map(ord, i)
    res = reduce(lambda x, y: x ^ y, bytes)
    return "%02x" % res

  def messageEncodeShort(self, receptorHead:str, command:str, parameter:str):
    '''
    Encode a message using the short communication format
    
    :param receptorHead: The number (formatted as a STRING) corresponding to an individual receptor head as set using the rotary switch
    :type receptorHead: str
    :param command: The command number (formatted as a STRING)
    :type command: str
    :param parameter: The 4-digit parameter code (formatted as a STRING)
    :type parameter: str
    '''
    bccable = ("%02d" % receptorHead) + command + parameter + "\x03" # 0x03 for ETX
    bccResult = self.computeBcc(bccable)
    return "\x02" + bccable + bccResult + "\x0D\x0A" # \x02 for STX, \x0D for CR, \x0A for LF

  def __assertBcc(self, bccable:str, actualBcc:str, i:str):
    '''
    Carries out the BCC Check between the actual and expected BCC
    
    :param bccable: The portion of the message bytestring to be BCCed
    :type bccable: str
    :param actualBcc: The actual BCC as sliced from the message
    :type actualBcc: str
    :param i: The full message bytestring
    :type i: str
    '''
    expectedBcc = self.computeBcc(bccable)
    if int(actualBcc, 16) != int(expectedBcc, 16):
      raise self.BCCException("BCC check failed, expected BCC '%s', got '%s', received '%s'." % (expectedBcc, actualBcc, i))

  def messageDecodeShort(self, i:str):
    '''
    Decodes a message in the short communication format
    
    :param i: The message bytestring to be decoded
    :type i: str
    '''
    self.__assertBcc(i[1:10], i[10:12], i)
    receptorHead = int(i[1:3])
    command = i[3:5]
    parameter = i[5:9]
    return (receptorHead, command, parameter)

  def messageDecodeLong(self, i:str):
    '''
    Decodes a message in the long communication format
    
    :param i: The message bytestring to be decoded
    :type i: str
    '''
    def dataToNumber(i):
      if i == "      ":
        return None
      else:
        v = int(i[1:5])
        e = int(i[5]) - 4
        r = v * (10**e)
        return -r if i[0] == '-' else r
    self.__assertBcc(i[1:28], i[28:30], i)
    receptorHead = int(i[1:3])
    command = i[3:5]
    status = i[5:9]
    data1 = dataToNumber(i[9:15])
    data2 = dataToNumber(i[15:21])
    data3 = dataToNumber(i[21:27])
    return (receptorHead, command, status, (data1, data2, data3))

  def sendShort(self, receptorHeadNumber:str, command:str, parameter:str):
    '''
    Send a message using the short communication format
    
    :param receptorHeadNumber: The number (formatted as a STRING) corresponding to an individual receptor head as set using the rotary switch
    :type receptorHeadNumber: str
    :param command: The command number (formatted as a STRING)
    :type command: str
    :param parameter: The 4-digit parameter code (formatted as a STRING)
    :type parameter: str
    '''
    encoded = self.messageEncodeShort(receptorHeadNumber, command, parameter)
    ret = self.ftdic.writeData(encoded)
    if ret < 0:
      print('ftdi_write_data failed: %d (%s)' %
            (ret, ftdi.get_error_string(self.ftdic)))
    else:
      print('ftdi_write_data wrote %d bytes' % ret)
  
  def checkStatus(self, decoded:tuple):
    '''
    Check the returned message for status updates, including errors and battery level
    
    :param decoded: Decoded message
    :type decoded: tuple
    '''
    status = str(decoded[2])

    #Check for errors
    errorCode = status[1]
    if errorCode == " " or errorCode == "7":
      pass
    elif errorCode == "1":
      raise self.PowerOffError("Receptor power head switched off, restart the T-10A")
    elif errorCode == "2":
      raise self.EEPROMError("EEPROM error 1, restart the T-10A")
    elif errorCode == "3":
      raise self.EEPROMError("EEPROM error 2, restart the T-10A")
    elif errorCode == "5":
      raise ValueError("Measure value is over range")
    
    #Check battery level
    battCode = status[3]
    if battCode == "0" or battCode == "2":
      pass
    elif battCode == "1" or battCode == "3":
      raise self.LowBatteryError("Low battery, change battery immediately and discard most recent measurement")

  def receiveShort(self):
    '''
    Receives a message from the device in the short communication format and checks for status updates. Returns the decoded message
    '''
    encoded = self.ftdic.readData(self.__messageLengthShort)
    decoded = self.messageDecodeShort(encoded)
    self.checkStatus(decoded)
    return decoded

  def receiveLong(self):
    '''
    Receives a message from the device in the long communication format and checks for status updates. Returns the decoded message
    '''
    encoded = self.ftdic.readData(self.__messageLengthLong)
    decoded =  self.messageDecodeLong(encoded)
    self.checkStatus(decoded)
    return decoded

  def __init__(self, ftdic:FtdiContext):
    self.ftdic = ftdic



class Protocol:

  class ProtocolException(Exception): pass

  def switchToPcConnectionMode(self):
    '''
    Executes command 54 to set the T-10A to PC connection mode
    '''
    self.messenger.sendShort("00", "54", "1   ")
    responseActual = self.messenger.receiveShort()
    responseExpected = ("00", "54", "    ")
    if responseActual != responseExpected:
      raise self.ProtocolException('wrong PcConnectionMode response, expected "%s", got "%s".' % (responseExpected, responseActual))
    time.sleep(0.5)

  def readMeasurementData(self, receptorHeadNumbers:tuple, hold:bool, ccf:bool, range):
    '''
    Executes command 10 to read measurement data or set the inital measurement conditions.
    
    :param receptorHeadNumber: A tuple containing the integer numbers corresponding to each receptor head
    :type receptorHeadNumber: tuple
    :param hold: Set the HOLD function to HOLD (True) or RUN (False)
    :type hold: bool
    :param ccf: Toggle Colour Correction Factor (CCF) function ENABLED (True) or DISABLED (False)
    :type ccf: bool
    :param range: Set the range of the measurement. Use "auto" to switch to automatic mode or enter an integer / float value in lux for the upper limit of the measurement range.
    '''
    if hold:
      holdCode = "1"
    else:
      holdCode = "0"
    
    if ccf:
      ccfCode = "2"
    else:
      ccfCode = "3"

    if range == "auto":
      rangeCode = "0"
    else:
      if float(range) >= 0 and float(range) <= 29.99:
        rangeCode = "1"
      elif float(range) > 29.99 and float(range) <= 299.9:
        rangeCode = "2"
      elif float(range) > 299.9 and float(range) <= 2999:
        rangeCode = "3"
      elif float(range) > 2999 and float(range) <= 29999:
        rangeCode = "4"
      elif float(range) > 29999 and float(range) <= 299999:
        rangeCode = "5"
      else:
        raise ValueError("Error invalid range setting")
    
    for receptorHeadNumber in receptorHeadNumbers:
      self.messenger.sendShort(f"{receptorHeadNumber:02d}", "10", holdCode + ccfCode + rangeCode + "0")
      receivedMessage = self.messenger.receiveLong() #(receptorHead, command, status, (data1, data2, data3))
      receivedData = receivedMessage[3]
      if receivedMessage[0] == receptorHeadNumber:
        with open(f"Receptor{receptorHeadNumber:02d}_data.csv", "a") as f:
          f.write(f"{receivedData[0]}, {receivedData[1]}, {receivedData[2]}\n")
      else:
        raise self.ProtocolException("Returned receptor head number did not match!")
      
    time.sleep(0.5)
    
  def setHoldStatus(self, hold:bool):
    '''
    Executes command 55 to toggle HOLD status between HOLD (True) and RUN (False)
    
    :param hold: Description
    :type hold: bool
    '''
    if hold:
      self.messenger.sendShort("99", "55", "1  0")
    else:
      self.messenger.sendShort("99", "55", "0  0")
    time.sleep(0.5)
  
  def clearPastIntegratedData(self, receptorHeadNumber:int): #TODO: Integrated data collection functions still WIP
    '''
    Executes command 28 to clear integration data stored in the T-10A
    
    :param receptorHeadNumber: The integer number corresponding to an individual receptor head as set using the rotary switch
    :type receptorHeadNumber: int
    '''
    self.messenger.sendShort(f"{receptorHeadNumber:02d}", "28", "    ")

  def __init__(self, messenger:Messenger):
    self.messenger = messenger



def main():
  while True:
    Ftdic = None
    try:
      Ftdic = FtdiContext()
      messenger = Messenger(Ftdic)
      protocol = Protocol(messenger)
      receptors = (0)

      protocol.switchToPcConnectionMode()
      #TODO: Clear send and receive buffers - how??

      protocol.readMeasurementData(receptors, hold=False, ccf=False, range="auto") # set measurement conditions
      time.sleep(3) # 3s for Auto and 1s for Manual
      protocol.readMeasurementData(receptors, hold=False, ccf=False, range="auto") # Take a measurement with the same settings. Loop command to take multiple measurments

    except Exception as e:
      print(str(e), file=sys.stderr)
    finally:
      Ftdic.endConnection()
      time.sleep(15)



if __name__ == "__main__":
    main()

