# API reference

High-level public surface. Full signatures come from the installed package.

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   create_array
   load_array
   compute_with_policy
   XYZConfig
   WMSConfig
   WMTSConfig
   service.WCSConfig
   types.CRS
   types.Format
```

Service helpers also live under `tilearray.service` (`WCSService`, `WMSService`, `WMTSService`, `XYZService`, registry helpers). Arrays from `create_array` are north-up (row 0 = north).
