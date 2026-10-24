# 镜像清单（2026-10-24）

所有镜像都是 **只用于 `fastboot boot`**（不写分区）。按下面的顺序试。

## 推荐顺序

| # | 镜像 | 大小 | 用途 |
| --- | --- | --- | --- |
| 1 | `boot-uefi-hello-gz3.img` | 1.9 MB | gzip ramdisk：验证 ABL 是否会解压（范围应变成 vendor+64 MiB） |
| 2 | `boot-uefi-hello-lz4dec.img` | 2.1 MB | LZ4 legacy ramdisk + 固件解码器 + 5 个 anchor |
| 3 | `boot-uefi-hello-dtbpay.img` | 69 MB | payload 内嵌在 FDT 的 totalsize 里，验证 ABL 是否整块复制 |
| 4 | `boot-uefi-diskdbg.img` | 1.8 MB | 已知能进 UniXNU 界面（基线对照） |

## 校验值

```
c56d9fdd895d942c77d5dfa68e82c20864aa953626f6bfdfcecf7facb0e89b9e  boot-uefi-hello-gz3.img
76dbc3cd06e218f9531999b902762b87873ee38d2d65765d865463712414d058  boot-uefi-hello-lz4dec.img
73a30ef92fd41334a5737bbb72b04a3f4f11ba9f6a9ab26e5ee39da332e69ba1  boot-uefi-hello-dtbpay.img
75f94150c7d24a3cbda8a50b92e24ebad9217091b0101b32b1a4ca76e56d0c6b  boot-uefi-diskdbg.img
```

## 怎么烧

```sh
adb reboot bootloader
fastboot devices
fastboot boot boot-uefi-hello-gz3.img
```

- 只 boot，不 flash；长按电源 10 秒即可回到 Android；
- 屏幕现象按 [DEVICE-TEST.zh.md](DEVICE-TEST.zh.md) 记录；
- `boot-uefi-*.json` 是每个镜像的构建报告（FD 基址/大小、gzip、DTB、ramdisk 的哈希）。

## 已下线的中间产物

`boot-uefi-only`、`boot-uefi-diskup`、`boot-uefi-debugcon`、`boot-uefi-hello`、
`boot-uefi-hello-up/down/new/full` 是排查过程中的版本，保留在 `out/` 里但不再推荐。