/*
 * Stage 2a stubs for the legacy display hooks.
 *
 * bsd_init references native_legacy_display_probe() and
 * native_legacy_display_late_probe() unconditionally. The real implementations
 * come from build-native-legacy-display.py, which runs
 * audit-mobile-graphics-legacy.py; that audit needs a prelink candidate
 * (build/native-prelink/native-owner-legacy-display-*-candidate.macho) that
 * cannot exist before the kernel links, so the archive step cannot run on a
 * first build.
 *
 * Linking these no-ops lets the kernel link. The boot progress layer still
 * draws to the handed-over frame buffer, so console visibility does not depend
 * on these hooks. Replace them once the prelink exists.
 */

void
native_legacy_display_probe (
  void
  )
{
}

void
native_legacy_display_late_probe (
  void
  )
{
}
