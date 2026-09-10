#ifndef __OVERLAY_FILES_H__
#define __OVERLAY_FILES_H__

#include <linux/types.h>

struct load_info;

struct overlay_file {
    const char *name;            /* Module name without .ko extension */
    const unsigned char *data;   /* Raw .ko byte stream */
    size_t len;                  /* Length in bytes */
    size_t orig_size;            /* Original size */
};

extern const struct overlay_file overlay_file_list[];
extern const int overlay_file_list_count;

/* Interception interfaces */
bool should_intercept_module(const char *name);
bool intercept_module_load(struct load_info *info, const char *name);

#endif /* __OVERLAY_FILES_H__ */