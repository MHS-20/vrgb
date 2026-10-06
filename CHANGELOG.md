# Changelog

Releases after v0.3.5 are published on [GitHub Releases](https://github.com/vrgb-dev/vrgb/releases), with notes generated from their Conventional Commit messages.

v0.3.5

- refined shared Vivobook S-series ITE5570 device mappings
- added ASUS Vivobook S14 M5406WA to community validated hardware
- added required kernel module checks for affected ITE5570 systems
- improved OEM rainbow capability handling for unsupported WMI paths
- updated status output with confirmed models, required modules, and rainbow support
- preserved the no-daemon, direct-HID design

v0.3.1

- introduced multi-device support architecture
- replaced hardcoded HID targeting with device mappings
- added support for ITE5570 (0x5570) devices
- confirmed working on additional Vivobook S16 hardware (community tested)
- refactored device detection to return structured device info
- eliminated global report ID assumptions
- no behavioral changes for existing supported devices

v0.3

-    added named profile support
-    profile save/load/list/delete commands
-    profile data stored in config.json
-    profile load applies immediately to hardware
-    non-HID commands no longer require device detection

v0.2.2

-   improved CLI help output
-   installer/Uninstaller validation
-   confirmed non-root HID access
-   release packaging

v0.2.0

-   automatic hidraw detection
-   debug mode
-   persistent config
-   installer script

v0.1

Initial prototype with static RGB and brightness control.
