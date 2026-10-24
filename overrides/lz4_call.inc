                if ((mVolumeBase == NULL) && (Length > 4)) {
                  UINTN  Lz4Bytes = 0;
                  UINT8  *Lz4Base = Lz4FindPayload (Range, (UINTN)Length, &Lz4Bytes, IsPayloadVolume);
                  if (Lz4Base != NULL) {
                    mVolumeBase  = Lz4Base;
                    mVolumeBytes = Lz4Bytes;
                    DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: payload at %lx (lz4 decompressed)\n", (UINT64)(UINTN)Lz4Base));
                  }
                }
