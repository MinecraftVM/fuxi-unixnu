# 只让 macOS 编内核，其余在本地组装

内核步骤需要 Xcode，但 `package / firmware / payload / bootimg` 都不需要。
工作流的 **`mode=kernel`** 只跑内核并上传 `kernel/`；把它拿回来就能在 Linux 上
把 boot.img 组装出来，省下大部分 macOS runner 时间。

## 0. 一条命令（推荐）

```sh
bash fuxi-unixnu/scripts/assemble-full.sh --kernel /path/to/kernel.macho --chip-id <16 位十六进制>
```

脚本会依次完成：payload（ADT + FAT + panic 屏）→ 编固件（如果还没有 FD）→ 打包 boot.img，
最后打印 sha256。需要 `python3 ≥ 3.11`、`mtools`、`ld.lld`/`llvm-nm` 在 PATH 上。

如果真机测试表明 ABL 只解压**压缩的 ramdisk**，加 `--gzip-ramdisk`（64 MiB 的 FAT 会压到 ~77 KB）。

下面是拆开的分步命令。

## 1. 在 Actions 上只编内核

Run workflow：

| 输入 | 值 |
| --- | --- |
| mode | `kernel` |
| config | `console` |
| chip_id | 16 位十六进制（payload 用，这里不影响内核） |
| 其余 | 默认 |

产物 `fuxi-kernel-<run>` 里有：

```
unixnu/out/fuxi/kernel/kernel.macho         预链接好的内核
unixnu/out/fuxi/kernel/kernel.json          预链接报告
out/logs/*.log                              构建日志
```

## 2. 本地准备（Linux，无 root 也行）

按 [BUILD-LOCAL.zh.md](BUILD-LOCAL.zh.md) 装好 LLVM、mtools、Python 3.13，并在
设备仓库上应用构建修正：

```sh
$PY fuxi-unixnu/scripts/apply-fixes.py --uefi uefi-fuxi --device unixnu-fuxi --console
```

## 3. 本地组装 payload

把内核放到驱动默认读取的位置：

```sh
mkdir -p UniXNU/out/fuxi/kernel
cp /path/to/artifact/unixnu/out/fuxi/kernel/kernel.macho UniXNU/out/fuxi/kernel/
```

然后（payload 步骤会做 ADT、写 FAT、注入 panic 屏）：

```sh
cd UniXNU
$PY ./unixnu build payload --uefi ../uefi-fuxi --unique-chip-id 0123456789abcdef
```

> panic 屏注入需要 `ld.lld` 和 `llvm-nm` 在 PATH 上（LLVM 发行包自带）。
> 如果它报符号不匹配，说明 kernel.macho 不是这棵树编的——用同一次构建的产物。

产物：`out/fuxi/payload/payload.img`（192 MiB）与 `payload.json`。

## 4. 本地编固件（带 payload 发现补丁）

```sh
cd UniXNU
$PY ./unixnu build firmware --uefi ../uefi-fuxi   # 需要先 apply-fixes 过
```

## 5. 打包 boot.img

```sh
$PY fuxi-unixnu/scripts/pack-bootimg-v3.py \
  --uefi uefi-fuxi \
  --fd uefi-fuxi/Build/fuxiPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd \
  --dtb uefi-fuxi/Resources/DTBs/fuxi.dtb \
  --ramdisk UniXNU/out/fuxi/payload/payload.img \
  --out out/boot-fuxi.img
```

打包器会打印 payload 的 FAT 信息，并逐段回读校验（gzip → shim+FD、DTB、ramdisk）。

## 6. 真机

```sh
fastboot boot out/boot-fuxi.img
```

第一次能看到的是 DarwinBoot 的交接日志和 UniXNU 的菜单；console 配置的内核没有自己的
屏幕输出（见 [FULL-MODE.zh.md](FULL-MODE.zh.md) 关于 boot progress 的说明）。