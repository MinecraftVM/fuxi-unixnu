# 镜像清单（2026-10-24）

所有镜像都是 **只用于 `fastboot boot`**（不写分区）。按下面的顺序试。

## 推荐顺序

| # | 镜像 | 固件基线 | 大小 | 用途 |
| --- | --- | --- | --- | --- |
| 1 | `boot-uefi-hello-probe2.img` | 最新诊断 | 69 MB | 打印 initrd 范围与三个 anchor 的 hexdump，再按字节搜 `FUXIESP` |
| 2 | `boot-uefi-hello-kdtb2.img` | 最新诊断 | 69 MB | payload 放 kernel 字段 DTB 之后，FDT 魔数扫描定位（完全绕开 ramdisk） |
| 3 | `boot-uefi-hello-label64.img` | 最新诊断 | 69 MB | 只有字节级标签搜索的简洁版 |
| 4 | `boot-uefi-diskdbg.img` | 旧基线 + 不 halt | 1.8 MB | 已知能进 UniXNU 界面（Restart / Power off） |

## 校验值

```
b35ef477d83219ee5bc2ad60d32107829cdf79292e79e97f7725cf6c72dd1d28  boot-uefi-hello-probe2.img
1348ee06767fd09872043e9efd5511f9e7c8e5fc9404e5804a3501f1cbd83686  boot-uefi-hello-kdtb2.img
375cd0a7614cd291215a1460d6802d733a66ff7aacb510091fafa5e44d4c67a2  boot-uefi-hello-label64.img
75f94150c7d24a3cbda8a50b92e24ebad9217091b0101b32b1a4ca76e56d0c6b  boot-uefi-diskdbg.img
```

## 怎么烧

```sh
adb reboot bootloader
fastboot devices
fastboot boot boot-uefi-hello-probe2.img
```

- 只 boot，不 flash；长按电源 10 秒即可回到 Android；
- 屏幕现象按 [DEVICE-TEST.zh.md](DEVICE-TEST.zh.md) 记录；
- `boot-uefi-*.json` 是每个镜像的构建报告（FD 基址/大小、gzip、DTB、ramdisk 的哈希）。

## 已下线的中间产物

`boot-uefi-only`、`boot-uefi-diskup`、`boot-uefi-debugcon`、`boot-uefi-hello`、
`boot-uefi-hello-up/down/new/full` 是排查过程中的版本，保留在 `out/` 里但不再推荐。