# rmectl

A basic driver & CLI utility for the mixer and configuration controls in RME Digiface interfaces.

Can probably be made to work with other similar RME devices, but I do not have them to test/add support.

Uses libusb1, tested on Linux. Untested on other OSes, might conflict with RME's official drivers.

Tested using the RME vendor class firmware setting, but I suspect it will work with the class-compliant mode too.
