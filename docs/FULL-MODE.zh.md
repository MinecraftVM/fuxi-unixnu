# full 模式（带 XNU 内核）为什么可能第一次跑不过

`uefi` 模式只用 EDK II 就能完成；`full` 模式要经过 UniXNU 的内核步骤，而
**fuxi 设备仓库现在是 stage 2a 的中间状态**：能编、能链接的静态库链还没补齐。
这篇文档把静态分析能看出来的坑写清楚，免得你把时间花在猜上。

## 1. 内核步骤实际做什么

`unixnu build kernel`（`scripts/unixnu/kernel.py`）按这个顺序：

1. 打 `patches/xnu` 和 NativePrelink 的补丁，跑 `prepare-xnu-source.py`；
2. 没有 `build/xnu/.../startup.o.json` 时先跑一次 **bootstrap pass**
   （`XNU_BOOTSTRAP=1`、`XNU_BOOT_PROGRESS=0`），只为留下编译记录；
3. 从 `refer/IOStorageFamily` 生成 `build/storage-abi` 头文件；
4. **如果 defconfig 有 `CONFIG_XNU_BUILD_ARCHIVES=y`**，按 `board.toml [kernel] archives`
   顺序跑每个静态库脚本；
5. 正式链接内核（`LD_KERNEL_ARCHIVES` 里会塞进 `libBootProgress.a`、`libNativeAES.a`、
   `XNU_EXTRA_LD_ARCHIVES` 等）；
6. `prelink`：把编译出的内核和原厂 kernelcache 预链接成 `kernel.macho`。

## 2. 两个已经能在代码里确认的缺口

### 2.1 归档脚本没有被启用，但链接仍然要它们

- `configs/fuxi_defconfig` **没有** `CONFIG_XNU_BUILD_ARCHIVES=y`；
  `build_archives()` 第一行就是 `if not ...get('XNU_BUILD_ARCHIVES'): return`，
  所以 `board.toml [kernel] archives` 里列的 11 个 builder **一个都不会跑**。
- 但链接阶段并不看这个开关：
  - `CONFIG_XNU_BOOT_PROGRESS=y` → `build-xnu-4k-baseline.sh` 无条件
    `-Wl,-force_load,$ROOT/build/boot-progress/libBootProgress.a`；
  - `CONFIG_XNU_EXTRA_LD_ARCHIVES="{tree}/build/legacy-stub/libLegacyStub.a"` → 直接拼进链接参数；
  - `CONFIG_XNU_NATIVE_AES=y` → 需要 `build/native-aes/libNativeAES.a`。

在一棵全新的 CI checkout 上，这三个 `.a` 都不存在，链接大概率报
`ld: library not found`。作者本机大概率是之前手动跑过这些脚本，文件还在 `build/` 里。

还有一层：`build-native-aes-bindings.py` 才是 `libNativeAES.a` 的生产者，而
`build-xnu-4k-baseline.sh` 里对 `CONFIG_XNU_NATIVE_AES=y` 直接写着
“NativeAES currently requires the qualified original AES/crypto prelink closure”。
也就是说 `fuxi_defconfig` 里的 `CONFIG_XNU_NATIVE_AES=y` 本身就需要一份
预链接基线，而那份基线只能由一次更早的构建产生。

**如果要修**：给 defconfig 加 `CONFIG_XNU_BUILD_ARCHIVES=y`，并把
`board.toml` 里和存储有关的两项先去掉（`CONFIG_XNU_NATIVE_UFS=n`，defconfig 的注释也是这个意思）：

```toml
archives = [
  ...
  # "audit-storage-interface.py",       # 需要 live capture，仓库里没有
  # "build-arm-driver.py NativeUfs",    # 需要上面那份 ABI
  ...
]
```

这一步我在本机无法验证（没有 macOS），所以**没有**默认改上游的 defconfig。

### 2.2 静态库脚本需要“prelink 候选”，而候选只能由上一轮产生

- `scripts/build-native-aes-bindings.py` 读
  `build/native-prelink/native-defaults-candidate.macho` + `.json`；
- `scripts/audit-storage-interface.py` 读
  `build/native-prelink/native-apfs-load-positive.macho` + `.json`，还要求一次 live 观察；
- 这些文件不在任何仓库里（官方 macOS workflow 的 `pre_build` 输入就是为这种
  “bootstrap 候选”准备的）。

官方构建文档给的 bootstrap 示例是：

```sh
./unixnu defconfig <bootstrap_defconfig> -D "$DEVICE_DIR"
./unixnu build kernel -D "$DEVICE_DIR"
mkdir -p out/<board>/tree/build/native-prelink
cp out/<board>/kernel/kernel.macho out/<board>/tree/build/native-prelink/<candidate>.macho
```

对应到 fuxi，候选名从上面两个脚本里读出来是：
`native-defaults-candidate` 和 `native-apfs-load-positive`。但 fuxi 仓库里
**没有** bootstrap_defconfig，也没有作者用的 pre_build 配方，所以这一段只能算推断。

## 3. 因此建议的推进方式

1. 先跑通 `uefi` 模式（有现成镜像，见 README）。
2. 再跑 `full`，**接受第一次可能失败**；失败后：

   ```sh
   # 下载 artifact，解压出 logs/
   python3 scripts/diagnose-logs.py logs/
   ```

   这个脚本会把 `library not found`、`native-prelink`、工具缺失、FD 溢出、
   panic 之类归类，并给出下一步。
3. 把 `diagnose-logs.py` 的输出 + `unixnu/out/fuxi/logs/kernel.log` 最后 50 行发回来，
   我按日志决定是“加 BUILD_ARCHIVES 并去掉 storage 归档”，还是“补 pre_build 的 bootstrap 配方”。

## 4. 为什么我不直接改 defconfig

- 上游正在高频推进（2026-10-22 ~ 10-24 几十个提交），今天补的静态库链明天可能就被作者自己的重构取代；
- 这些改动只有 macOS 上跑一次内核构建才能验证，我这边没有 Xcode，改了也无法证明是对的；
- 默认保持上游可复现，把“已知缺口 + 触发条件 + 修法”写清楚，比猜一个必挂的配置更有用。

## 5. 已确认可用的部分

- EDK II 固件、UniXNU 包、BootShim、v3 打包在 Linux 上实测可复现（见 `BUILD-LOCAL.zh.md`）；
- `payload` 步骤的输入我们全部静态检查过：
  - 内核命令行 892 字节 < 1000 字节上限，40 个参数都符合 `check_args` 的正则；
  - ADT 工具要求 `--unique-chip-id` 与 `--chip-id` 同时给，board.toml 两者都有
    （`chip_id="207"` 按十六进制解析 = 0x207，和 DTB 的 `qcom,msm-id` 一致）；
  - `ipad_identity = true` 会传 `--ipad-identity`，ADT 里才用 iPad 身份而不是 sheng 的。