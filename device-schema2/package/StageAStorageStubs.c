/*
 * Stage 2a definitions for the symbols the kernel references but this board does
 * not build yet: the storage transports, the owner root, the touch input probe,
 * the RAM block device and the legacy display transfer.
 *
 * Boot progress links with the audited UFS driver, the owner-root code and the
 * virtio transport selected (see build-xnu-4k-baseline.sh), and it reads the
 * counters of every other subsystem it can show. The archives that normally
 * answer them are research instruments: build-native-virtio-bindings.py needs a
 * live storage capture and build-verified-root-probe.py needs a completed
 * verification of the original OS volume. Neither input exists in a clean
 * checkout.
 *
 * The references in BootProgress.c are marked weak_import, but the kernel is
 * linked as a static Mach-O, where ld64 still requires every reference to be
 * defined; hence the definitions below rather than a linker flag.
 *
 * Nothing here reports success:
 *   - fuxi has no virtio block device and no RAM block device, so their counters
 *     stay zero and boot progress takes its UFS branch;
 *   - the owner root has no policy bundle and no trusted key on this board, so
 *     vr_open() (kits/VerifiedRoot, linked from its real sources) refuses every
 *     volume instead of accepting one;
 *   - there is no touch driver yet, so the touch rows are empty and the count is
 *     zero, which is what the overlay draws;
 *   - the sheng PMGR power driver is an instrument of its own, so the poll is a
 *     no-op and the cpufreq row stays empty.
 *
 * Replace the definitions here with the real archives once their inputs exist;
 * until then they make the absence explicit instead of hiding it.
 */
#include <stddef.h>
#include <stdint.h>

/* BootProgress.c reads these as plain variables; zero means "no virtio". */
volatile uint32_t native_virtio_ready;
volatile uint32_t native_virtio_read_requests;
volatile uint32_t native_virtio_read_failures;
volatile uint64_t native_virtio_verified_blocks;

/* No RAM block device on this board either. */
volatile uint32_t native_ram_block_sparse;
volatile uint64_t native_ram_block_bytes;
volatile uint64_t native_ram_block_resident_bytes;

/* No touch controller driver: the overlay has no rows and no samples. */
char native_touch_rows[4][120];
volatile uint32_t native_touch_seq;
volatile uint32_t native_touch_count;
volatile int native_touch_xs[12];
volatile int native_touch_ys[12];
volatile uint32_t native_touch_stage_req;

/* The legacy display probe is stubbed out (LegacyDisplayStub.c), so it never
   transfers anything and never reports a status. */
volatile int native_legacy_transfer_status;

/* The owner-root mount path did not accept a volume. */
volatile int native_owner_root_accepted;

/* The policy table the owner-root mount path walks: no entries, no trusted key.
   verified_root_key is VR_KEY_BYTES of zeroes, which fails the kit's own magic
   and key checks, so vr_open() fails closed. */
const uint64_t verified_root_policy_count = 0;
const uint64_t verified_root_policies[2] = {0, 0};
const uint8_t verified_root_key[395];

/* Declared weakly by BootProgress.c, so an absent definition must still link. */
void
sheng_cpufreq_poll (
  void
  )
{
}
