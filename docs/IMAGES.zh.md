# 镜像清单（2026-10-24）

所有镜像都是 **只用于 `fastboot boot`**（不写分区）。按下面的顺序试。

## 推荐顺序

| # | 镜像 | 固件基线 | 大小 | 用途 |
| --- | --- | --- | --- | --- |
| 1 | `boot-uefi-hello-dt.img` | 上游 `a7f42bfc` + 全量扫描 | 203 MB | **payload 发现**：设备树下方 256 MiB + `/chosen` ±4 MiB + Kernel 窗口 + 常规内存 |
| 2 | `boot-uefi-diag.img` | 旧基线 + 诊断 | 203 MB | 打印 `FUXIDT:`（x0、`/chosen` initrd）、`FUXIMAP:`（内存布局）、`FUXIRAMDISK:` |
| 3 | `boot-uefi-diskdbg.img` | 旧基线 + 不 halt | 1.8 MB | 已知能进 UniXNU 界面（Restart / Power off） |
| 4 | `boot-uefi-probe.img` | 旧基线 + FUXIPROBE | 1.8 MB | 打印 GOP 状态和 FUXIESP 命中后停机 |
| 5 | `boot-uefi-hello-label64.img` | 最新诊断 | 69 MB | 在 `/chosen` initrd 范围内**按字节**搜 `FUXIESP`（解决非 512 对齐） |
| 6 | `boot-uefi-hello-latest.img` | 上游 `a7f42bfc` + 兜底 | 203 MB | 与 1 类似，但没有 below-DT 扫描 |

## 校验值

```
08280548d0042cd1ff92679d6e5630b240d296343a2b7f2bd5ca893a707bae31  boot-uefi-hello-dt.img
2318b8c8550a3bc9ee2042760835e9f8887e0b442e709fc81bc58cea30a338b1  boot-uefi-diag.img
75f94150c7d24a3cbda8a50b92e24ebad9217091b0101b32b1a4ca76e56d0c6b  boot-uefi-diskdbg.img
614717cf98f2644f952b6c9b53e869086789c7a25cd2fb6dc8543a6cf6f43ac0  boot-uefi-probe.img
9a8d2c6492f679c07e0b31fb5a9544480e0bbee2c147edb4796983235f4d88a5  boot-uefi-hello-latest.img
```

## 怎么烧

```sh
adb reboot bootloader
fastboot devices
fastboot boot boot-uefi-hello-dt.img
```

- 只 boot，不 flash；长按电源 10 秒即可回到 Android；
- 屏幕现象按 [DEVICE-TEST.zh.md](DEVICE-TEST.zh.md) 记录；
- `boot-uefi-*.json` 是每个镜像的构建报告（FD 基址/大小、gzip、DTB、ramdisk 的哈希）。

## 已下线的中间产物

`boot-uefi-only`、`boot-uefi-diskup`、`boot-uefi-debugcon`、`boot-uefi-hello`、
`boot-uefi-hello-up/down/new/full` 是排查过程中的版本，保留在 `out/` 里但不再推荐。