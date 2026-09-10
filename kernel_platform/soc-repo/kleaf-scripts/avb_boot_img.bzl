def sign_boot_img(ctx):
    inputs = []
    inputs += ctx.files.artifacts
    inputs += ctx.files.avbtool
    inputs += ctx.files.key

    outputs = ctx.actions.declare_file("{}/boot.img".format(ctx.label.name))

    boot_img = None
    for artifact in ctx.files.artifacts:
        if artifact.basename == "boot.img":
            boot_img = artifact
            break

    if not boot_img:
        fail("artifacts must include file named \"boot.img\"")

    proplist = " ".join(["--prop {}".format(x) for x in ctx.attr.props])

    command = """
    cp {boot_img} {boot_dir}/{boot_name}
    {tool} add_hash_footer --image {boot_dir}/{boot_name} --algorithm SHA256_RSA4096 \
            --key {key} --partition_size {boot_partition_size} --partition_name boot \
            {proplist}
    """.format(
        boot_img = boot_img.path,
        tool = ctx.file.avbtool.path,
        key = ctx.file.key.path,
        boot_dir = outputs.dirname,
        boot_name = outputs.basename,
        boot_partition_size = ctx.attr.boot_partition_size,
        proplist = proplist,
    )

    ctx.actions.run_shell(
        mnemonic = "SignBootImg",
        inputs = inputs,
        outputs = [outputs],
        command = command,
        progress_message = "Signing boot image from artifacts",
    )

    return [
        DefaultInfo(
            files = depset([outputs]),
        ),
    ]

avb_sign_boot_image = rule(
    implementation = sign_boot_img,
    doc = "Sign the boot image present in artifacts",
    attrs = {
        "artifacts": attr.label(
            mandatory = True,
            allow_files = True,
        ),
        "avbtool": attr.label(
            mandatory = True,
            allow_single_file = True,
        ),
        "key": attr.label(
            mandatory = True,
            allow_single_file = True,
        ),
        "boot_partition_size": attr.int(
            mandatory = False,
            default = 0x6000000,  # bytes, = 98304 kb
            doc = "Final size of boot.img desired",
        ),
        "props": attr.string_list(
            mandatory = True,
            allow_empty = False,
            doc = "List of key:value pairs",
        ),
    },
)

def pad_vendor_boot_img(ctx):
    inputs = []
    inputs += ctx.files.artifacts
    inputs += ctx.files.avbtool

    output = ctx.actions.declare_file("{}/vendor_boot.img".format(ctx.label.name))

    vendor_boot_img = None
    for artifact in ctx.files.artifacts:
        if artifact.basename == "vendor_boot.img":
            vendor_boot_img = artifact
            break

    if not vendor_boot_img:
        fail("artifacts must include file named \"vendor_boot.img\"")

    command = """
    cp {vendor_boot_img} {output}
    {tool} add_hash_footer --image {output} --algorithm NONE \
            --partition_size {partition_size} --partition_name vendor_boot
    """.format(
        vendor_boot_img = vendor_boot_img.path,
        output = output.path,
        tool = ctx.file.avbtool.path,
        partition_size = ctx.attr.partition_size,
    )

    ctx.actions.run_shell(
        mnemonic = "PadVendorBootImg",
        inputs = inputs,
        outputs = [output],
        command = command,
        progress_message = "Adding AVB footer and padding vendor_boot image",
    )

    return [DefaultInfo(files = depset([output]))]

avb_pad_vendor_boot_image = rule(
    implementation = pad_vendor_boot_img,
    doc = "Add an unsigned AVB hash footer and pad vendor_boot to its partition size",
    attrs = {
        "artifacts": attr.label(
            mandatory = True,
            allow_files = True,
        ),
        "avbtool": attr.label(
            mandatory = True,
            allow_single_file = True,
        ),
        "partition_size": attr.int(
            mandatory = True,
        ),
    },
)
