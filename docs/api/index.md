# API reference

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

Lower-level service clients live under `tilearray.service` (`WCSService`, `WMSService`, `WMTSService`, `XYZService`, plus registry helpers). Arrays from `create_array` are north-up (row 0 = north).
