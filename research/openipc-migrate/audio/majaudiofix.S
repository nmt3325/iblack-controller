@ libmajaudiofix.so - LD_PRELOAD fixes for the audio of OpenIPC majestic on Hi3518EV100
@ (iBlack / iErmu, Hi3518 MPP V1): the microphone channel count and the speaker volume.
@ See audio/README.md.
@
@ 1. Microphone.  majestic's audio-in init fills AIO_ATTR_S with u32ChnCnt = 2 (the I2S of the
@ inner codec takes 2/4/8/16 channels, also in mono mode; the stock firmware passes 2 as well)
@ and then re-uses that same u32ChnCnt as the channel count of its own audio pipeline.  The frame
@ callback only forwards channel 0 (mono PCM), so the Opus and AAC encoders are fed mono
@ samples while being told they are interleaved stereo: every Opus packet is a 10 ms SILK
@ *stereo* frame stamped as 20 ms.  Players (WebRTC, RTSP, MP4, Ogg) get half of the sound,
@ an octave too high, and conceal the gaps -> "noisy" microphone.
@
@ Fix: wrap HI_MPI_AI_SetPubAttr().  The driver still receives the caller's attributes
@ unchanged (ChnCnt = 2, both channels enabled as before).  Only after the call succeeded,
@ and only for mono (enSoundmode == 0) with u32ChnCnt == 2, the caller's struct is patched
@ to u32ChnCnt = 1, which majestic then uses as the pipeline channel count -> mono 20 ms
@ Opus frames with correct timestamps.  Everything else is passed through.
@
@ AIO_ATTR_S (hi_comm_aio.h): +0 enSamplerate, +4 enBitwidth, +8 enWorkmode,
@ +12 enSoundmode, +16 u32EXFlag, +20 u32FrmNum, +24 u32PtNumPerFrm,
@ +28 u32ChnCnt, +32 u32ClkSel
@
@ 2. Speaker volume.  majestic has no live speaker level for this vendor, and HI_MPI_AO_SetVolume
@ is a stub in the AO driver of MPP V1 (HI_ERR_AO_NOT_SUPPORT), so audio.outputVolume changes
@ nothing: everything plays at 0 dB.  The stock firmware scales the samples itself (talk volume
@ v -> (v - 50) * 0.6 dB).  The same here:
@  - HI_MPI_ADEC_CreateChn: majestic's init_audio_out creates its one ADEC channel (always
@    PT_LPCM = 23) whenever the speaker starts or is rebuilt.  After the real call succeeded,
@    audio.outputVolume is read through majestic's exported config_get_int() and turned into a
@    Q16 gain: 0 = mute, 1..100 = (v - 50) * 0.6 dB (-29.4 .. +30 dB), not set = 50 = 0 dB.
@    The result goes to syslog.  Another type, or no config_get_int (not majestic): 0 dB.
@  - HI_MPI_ADEC_SendStream: at 0 dB the call is passed through untouched.  Otherwise libmpi
@    gets a copy of the caller's AUDIO_STREAM_S whose pStream points at scaled s16le samples
@    ((s * gain + 0x8000) >> 16, saturated) on this function's stack; libmpi has copied them
@    when it returns.  The caller's struct and buffer are only read (the samples byte by byte,
@    so any alignment works).  An empty packet, a NULL pointer or more than 8192 bytes
@    (majestic sends 640, at most 2048) goes on unchanged, an odd last byte is copied as it is.
@
@ ADEC_CHN_ATTR_S (hi_comm_adec.h): +0 enType, +4 u32BufSize, +8 enMode, +12 pValue
@ AUDIO_STREAM_S (hi_comm_aio.h): +0 pStream, +4 u32PhyAddr, +8 u32Len, +16 u64TimeStamp,
@ +24 u32Seq (32 bytes)
@
@ Build: audio/build.sh (GNU as + ld only, linked against the musl libc.so of the rootfs).

	.syntax unified
	.arch armv5te
	.arm
	.text
	.align 2
	.global HI_MPI_AI_SetPubAttr
	.type HI_MPI_AI_SetPubAttr, %function
HI_MPI_AI_SetPubAttr:
	push	{r4, r5, r6, lr}
	mov	r4, r0			@ AUDIO_DEV AiDevId
	mov	r5, r1			@ const AIO_ATTR_S *pstAttr (caller's struct)
	mvn	r0, #0			@ RTLD_NEXT == (void *)-1 in musl
	adr	r1, .Lname
	bl	dlsym			@ musl dlsym uses lr to find the calling DSO
	cmp	r0, #0
	beq	.Lnosym
	mov	ip, r0
	mov	r0, r4
	mov	r1, r5
	blx	ip			@ real HI_MPI_AI_SetPubAttr from libmpi.so
	mov	r6, r0
	cmp	r6, #0
	bne	.Lout			@ failed: leave everything untouched
	cmp	r5, #0
	beq	.Lout
	ldr	r2, [r5, #12]		@ enSoundmode: 0 = AUDIO_SOUND_MODE_MONO
	cmp	r2, #0
	bne	.Lout
	ldr	r2, [r5, #28]		@ u32ChnCnt
	cmp	r2, #2
	moveq	r2, #1
	streq	r2, [r5, #28]		@ pipeline becomes mono
.Lout:
	mov	r0, r6
	pop	{r4, r5, r6, pc}
.Lnosym:
	mvn	r0, #0			@ report failure (-1) if libmpi's symbol is missing
	pop	{r4, r5, r6, pc}
.Lname:
	.asciz	"HI_MPI_AI_SetPubAttr"
	.align 2
	.size HI_MPI_AI_SetPubAttr, .-HI_MPI_AI_SetPubAttr

	.align 2
	.global HI_MPI_ADEC_CreateChn
	.type HI_MPI_ADEC_CreateChn, %function
HI_MPI_ADEC_CreateChn:
	push	{r4, r5, r6, lr}
	mov	r4, r0			@ ADEC_CHN AdChn
	mov	r5, r1			@ const ADEC_CHN_ATTR_S *pstAttr
	mvn	r0, #0			@ RTLD_NEXT
	adr	r1, .Lcreate_name
	bl	dlsym
	cmp	r0, #0
	beq	.Lcreate_nosym
	mov	ip, r0
	mov	r0, r4
	mov	r1, r5
	blx	ip			@ real HI_MPI_ADEC_CreateChn from libmpi.so
	mov	r6, r0
	cmp	r6, #0
	bne	.Lcreate_out		@ failed: the gain stays as it is
	mov	r0, #65536		@ 0 dB unless this is an LPCM channel
	cmp	r5, #0
	beq	.Lcreate_set
	ldr	r1, [r5]		@ enType
	cmp	r1, #23			@ PT_LPCM
	bleq	speaker_gain		@ r0 = gain for audio.outputVolume
.Lcreate_set:
	ldr	r1, .Lcreate_gain
.Lcreate_pc:
	add	r1, pc, r1
	str	r0, [r1]		@ the gain HI_MPI_ADEC_SendStream applies
.Lcreate_out:
	mov	r0, r6
	pop	{r4, r5, r6, pc}
.Lcreate_nosym:
	mvn	r0, #0			@ report failure (-1) if libmpi's symbol is missing
	pop	{r4, r5, r6, pc}
.Lcreate_gain:
	.word	.Lgain - (.Lcreate_pc + 8)
.Lcreate_name:
	.asciz	"HI_MPI_ADEC_CreateChn"
	.align 2
	.size HI_MPI_ADEC_CreateChn, .-HI_MPI_ADEC_CreateChn

@ speaker_gain() -> r0: the Q16 gain for audio.outputVolume, also written to syslog
	.align 2
	.type speaker_gain, %function
speaker_gain:
	push	{r4, r5, r6, lr}
	mov	r0, #0			@ RTLD_DEFAULT: majestic exports config_get_int
	adr	r1, .Lcfg_name
	bl	dlsym
	subs	ip, r0, #0
	moveq	r0, #65536		@ not inside majestic: 0 dB
	popeq	{r4, r5, r6, pc}
	adr	r0, .Lcfg_section
	adr	r1, .Lcfg_key
	blx	ip			@ config_get_int("audio", "outputVolume")
	cmp	r0, #0x80000000		@ INT_MIN: not set
	moveq	r0, #50			@ majestic's default
	cmp	r0, #0
	movlt	r0, #0
	cmp	r0, #100
	movgt	r0, #100
	mov	r4, r0			@ volume 0..100
	ldr	r1, .Lgain_table_off
.Lgain_table_pc:
	add	r1, pc, r1
	ldr	r5, [r1, r4, lsl #2]	@ its gain
	mov	r0, #6			@ LOG_INFO, in the facility of majestic's openlog()
	adr	r1, .Lgain_msg
	mov	r2, r4
	mov	r3, r5
	bl	syslog
	mov	r0, r5
	pop	{r4, r5, r6, pc}
.Lgain_table_off:
	.word	.Lgain_table - (.Lgain_table_pc + 8)
.Lcfg_name:
	.asciz	"config_get_int"
.Lcfg_section:
	.asciz	"audio"
.Lcfg_key:
	.asciz	"outputVolume"
.Lgain_msg:
	.asciz	"majaudiofix: speaker outputVolume %d -> gain %d/65536"
	.align 2
	.size speaker_gain, .-speaker_gain

	.align 2
	.global HI_MPI_ADEC_SendStream
	.type HI_MPI_ADEC_SendStream, %function
HI_MPI_ADEC_SendStream:
	push	{r4, r5, r6, r7, r8, r9, r10, lr}
	mov	r4, r0			@ ADEC_CHN AdChn
	mov	r5, r1			@ const AUDIO_STREAM_S *pstStream
	mov	r6, r2			@ HI_BOOL bBlock
	mvn	r0, #0			@ RTLD_NEXT
	adr	r1, .Lsend_name
	bl	dlsym
	subs	r7, r0, #0
	mvneq	r0, #0			@ report failure (-1) if libmpi's symbol is missing
	beq	.Lsend_ret
	mov	r9, sp
	mov	r1, r5			@ by default the caller's stream as it is
	ldr	r3, .Lsend_gain
.Lsend_pc:
	add	r3, pc, r3
	ldr	r8, [r3]		@ Q16 gain
	cmp	r8, #65536
	beq	.Lsend_call		@ 0 dB
	cmp	r5, #0
	beq	.Lsend_call
	ldr	r2, [r5, #8]		@ u32Len in bytes
	ldr	r10, [r5]		@ pStream
	cmp	r2, #0
	cmpne	r10, #0
	beq	.Lsend_call
	cmp	r2, #8192
	bhi	.Lsend_call
	add	r0, r2, #7
	bic	r0, r0, #7
	sub	sp, sp, r0		@ room for the scaled samples ...
	sub	sp, sp, #32		@ ... behind a copy of the AUDIO_STREAM_S
	ldm	r5, {r0, r1, r3, ip}
	stm	sp, {r0, r1, r3, ip}
	add	r0, r5, #16
	ldm	r0, {r0, r1, r3, ip}
	add	lr, sp, #16
	stm	lr, {r0, r1, r3, ip}
	add	lr, sp, #32
	str	lr, [sp]		@ copy.pStream = the scaled samples
	mov	ip, #32768
	sub	ip, ip, #1		@ 0x7fff
.Lsend_loop:
	subs	r2, r2, #2
	bmi	.Lsend_tail
	ldrb	r0, [r10], #1		@ s16le sample, byte by byte
	ldrsb	r3, [r10], #1
	orr	r0, r0, r3, lsl #8
	smull	r0, r3, r8, r0		@ r3:r0 = sample * gain
	adds	r0, r0, #32768		@ round
	adc	r3, r3, #0
	mov	r0, r0, lsr #16
	orr	r0, r0, r3, lsl #16	@ (sample * gain + 0x8000) >> 16
	mov	r3, r0, asr #15
	teq	r3, r0, asr #31		@ fits in 16 bits?
	eorne	r0, ip, r0, asr #31	@ no: saturate to 0x7fff or -0x8000
	strh	r0, [lr], #2
	b	.Lsend_loop
.Lsend_tail:
	cmn	r2, #1			@ an odd last byte goes on as it is
	ldrbeq	r0, [r10]
	strbeq	r0, [lr]
	mov	r1, sp			@ the copy
.Lsend_call:
	mov	r0, r4
	mov	r2, r6
	blx	r7			@ real HI_MPI_ADEC_SendStream from libmpi.so
	mov	sp, r9
.Lsend_ret:
	pop	{r4, r5, r6, r7, r8, r9, r10, pc}
.Lsend_gain:
	.word	.Lgain - (.Lsend_pc + 8)
.Lsend_name:
	.asciz	"HI_MPI_ADEC_SendStream"
	.align 2
	.size HI_MPI_ADEC_SendStream, .-HI_MPI_ADEC_SendStream

@ audio.outputVolume 0..100 -> Q16 gain: 0 (mute), then round(65536 * 10^((v - 50) * 0.03))
	.align 2
.Lgain_table:
	.word	0, 2221, 2379, 2550, 2732, 2927, 3137, 3361
	.word	3601, 3859, 4135, 4431, 4748, 5087, 5451, 5841
	.word	6259, 6706, 7186, 7700, 8250, 8841, 9473, 10150
	.word	10876, 11654, 12488, 13381, 14338, 15363, 16462, 17639
	.word	18901, 20253, 21701, 23253, 24916, 26698, 28608, 30653
	.word	32846, 35195, 37712, 40409, 43299, 46396, 49714, 53270
	.word	57079, 61162, 65536, 70223, 75245, 80627, 86393, 92572
	.word	99193, 106287, 113889, 122034, 130762, 140113, 150134, 160872
	.word	172377, 184706, 197916, 212070, 227237, 243489, 260904, 279563
	.word	299557, 320981, 343938, 368536, 394893, 423136, 453398, 485825
	.word	520571, 557802, 597695, 640442, 686246, 735326, 787916, 844267
	.word	904649, 969348, 1038676, 1112961, 1192559, 1277850, 1369241, 1467168
	.word	1572099, 1684535, 1805011, 1934104, 2072430

	.data
	.align 2
.Lgain:
	.word	65536			@ Q16 gain of the speaker, 65536 = 0 dB

	.section .note.GNU-stack,"",%progbits
