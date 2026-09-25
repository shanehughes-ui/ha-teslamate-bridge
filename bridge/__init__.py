"""ha-teslamate-bridge: any car in Home Assistant, presented to stock TeslaMate as a
Tesla Owner API.

Layout, and the rule that keeps it honest:

    units.py     km -> miles, km/h -> mph, psi -> bar          PURE
    sanitise.py  outlier rejection, freshness, heading, gear,  PURE
                 sleep verdict
    mapping.py   HA entity snapshot -> Owner API JSON          PURE
    config.py    where the entities live (add-on options)     no HA, file + env only
    hass.py      the ONE module that talks to Home Assistant  I/O
    app.py       the four HTTP endpoints TeslaMate calls      wiring

"Pure" means: no Home Assistant import, no network, no clock read, no file read.
CI asserts it, and the tests run without any of those installed. If a pure module
ever needs one of them, the separation has been lost, and that is worth failing.
"""

__version__ = "0.1.0"
