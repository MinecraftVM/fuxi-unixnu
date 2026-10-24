/** @file
  Legacy-LZ4 frame decoder for a ramdisk the bootloader copied without expanding.

  lz4 -l writes magic 0x184C2102 followed by blocks of [le32 size][data]; a block
  with the high bit set is stored verbatim. The vendor ramdisk on fuxi uses the
  same format, so the payload volume can be shipped compressed when the ABL only
  copies it.
**/
#include <Uefi.h>
#include <Library/BaseMemoryLib.h>
#include <Library/MemoryAllocationLib.h>

STATIC
UINT32
Lz4Read32 (
  IN CONST UINT8  *P
  )
{
  return (UINT32)P[0] | ((UINT32)P[1] << 8) | ((UINT32)P[2] << 16) | ((UINT32)P[3] << 24);
}

STATIC
BOOLEAN
Lz4BlockDecode (
  IN  CONST UINT8  *Src,
  IN  UINTN        SrcLen,
  OUT UINT8        *Dst,
  IN  UINTN        DstCap,
  OUT UINTN        *OutLen
  )
{
  CONST UINT8  *Ip   = Src;
  CONST UINT8  *Iend = Src + SrcLen;
  UINT8        *Op   = Dst;
  UINT8        *Oend = Dst + DstCap;

  while (Ip < Iend) {
    UINT32  Token = *Ip++;
    UINTN   Lit   = Token >> 4;
    UINTN   Match;
    UINTN   Offset;
    UINT8   *Mp;

    if (Lit == 15) {
      UINT8  S;
      do {
        if (Ip >= Iend) {
          return FALSE;
        }
        S = *Ip++;
        Lit += S;
      } while (S == 255);
    }
    if (((UINTN)(Iend - Ip) < Lit) || ((UINTN)(Oend - Op) < Lit)) {
      return FALSE;
    }
    CopyMem (Op, Ip, Lit);
    Ip += Lit;
    Op += Lit;
    if (Ip >= Iend) {
      break;
    }
    if ((UINTN)(Iend - Ip) < 2) {
      return FALSE;
    }
    Offset = (UINTN)Ip[0] | ((UINTN)Ip[1] << 8);
    Ip += 2;
    if ((Offset == 0) || ((UINTN)(Op - Dst) < Offset)) {
      return FALSE;
    }
    Match = (Token & 0xF) + 4;
    if ((Token & 0xF) == 15) {
      UINT8  S;
      do {
        if (Ip >= Iend) {
          return FALSE;
        }
        S = *Ip++;
        Match += S;
      } while (S == 255);
    }
    if ((UINTN)(Oend - Op) < Match) {
      return FALSE;
    }
    Mp = Op - Offset;
    if (Offset >= Match) {
      CopyMem (Op, Mp, Match);
      Op += Match;
    } else {
      UINTN  I;
      for (I = 0; I < Match; I++) {
        Op[I] = Mp[I];
      }
      Op += Match;
    }
  }
  *OutLen = (UINTN)(Op - Dst);
  return TRUE;
}

/**
  Decode a complete legacy-LZ4 frame. Returns the number of bytes written, or 0.
**/
UINTN
Lz4LegacyDecompress (
  IN  CONST UINT8  *Src,
  IN  UINTN        SrcLen,
  OUT UINT8        *Dst,
  IN  UINTN        DstCap
  )
{
  UINTN  Pos;
  UINTN  Out = 0;

  if ((SrcLen < 4) || (Lz4Read32 (Src) != 0x184C2102u)) {
    return 0;
  }
  Pos = 4;
  while (Pos + 4 <= SrcLen) {
    UINT32   Size   = Lz4Read32 (Src + Pos);
    BOOLEAN  Stored = (Size & 0x80000000u) != 0;
    UINTN    N      = Size & 0x7FFFFFFFu;
    Pos += 4;
    if ((N == 0) || (Pos + N > SrcLen)) {
      break;
    }
    if (Stored) {
      if (Out + N > DstCap) {
        break;
      }
      CopyMem (Dst + Out, Src + Pos, N);
      Out += N;
    } else {
      UINTN  Got = 0;
      if (!Lz4BlockDecode (Src + Pos, N, Dst + Out, DstCap - Out, &Got)) {
        break;
      }
      Out += Got;
    }
    Pos += N;
  }
  return Out;
}
/**
  Find a legacy-LZ4 frame in Range, decode it and accept it when IsVolume agrees.

  Returns the allocated payload buffer, or NULL. The caller owns the buffer.
**/
UINT8 *
Lz4FindPayload (
  IN  CONST UINT8  *Range,
  IN  UINTN        Length,
  OUT UINTN        *Bytes,
  IN  BOOLEAN      (*IsVolume)(CONST UINT8 *, UINT64 *)
  )
{
  UINTN  Offset;

  for (Offset = 0; (Offset + 4) <= Length; Offset++) {
    if (Lz4Read32 (Range + Offset) == 0x184C2102u) {
      UINTN    Pages = EFI_SIZE_TO_PAGES (0x4000000);
      UINT8    *Out  = (UINT8 *)(UINTN)AllocatePages (Pages);
      if (Out != NULL) {
        UINTN   Got   = Lz4LegacyDecompress (Range + Offset, Length - Offset, Out, 0x4000000);
        UINT64  Total = 0;
        if ((Got != 0) && IsVolume (Out, &Total)) {
          *Bytes = (UINTN)(Total * 512);
          return Out;
        }
        FreePages (Out, Pages);
      }
      /* A false magic in unrelated data is possible; keep looking. */
    }
  }
  return NULL;
}