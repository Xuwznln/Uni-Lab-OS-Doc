#ifndef UNILAB_SCHEDULER_ENGINE_V1_H
#define UNILAB_SCHEDULER_ENGINE_V1_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* ABI 1.0: high 16 bits = major, low 16 bits = minor. */
uint32_t uni_sched_abi_version(void);
/* Return 0: valid response JSON (ok may be false). Negative: no response. */
int32_t uni_sched_describe(const uint8_t *req, uint64_t req_len,
                          uint8_t **out, uint64_t *out_len);
int32_t uni_sched_create(const uint8_t *req, uint64_t req_len,
                        uint8_t **out, uint64_t *out_len);
int32_t uni_sched_decide(const uint8_t *req, uint64_t req_len,
                        uint8_t **out, uint64_t *out_len);
int32_t uni_sched_close(const uint8_t *req, uint64_t req_len,
                       uint8_t **out, uint64_t *out_len);
void uni_sched_free_buffer(uint8_t *ptr, uint64_t len);
#ifdef __cplusplus
}
#endif
#endif
