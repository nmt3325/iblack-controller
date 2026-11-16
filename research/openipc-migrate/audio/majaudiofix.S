@ libmajaudiofix.so - LD_PRELOAD fix for the microphone of OpenIPC majestic on Hi3518EV100
@ (iBlack / iErmu, Hi3518 MPP V1). See audio/README.md.
@
@ Problem: majestic's audio-in init fills AIO_ATTR_S with u32ChnCnt = 2 (the I2S of the inner
@ codec takes 2/4/8/16 channels, also in mono mode; the stock firmware passes 2 as well) and
@ then re-uses that same u32ChnCnt as the channel count of its own audio pipeline.  The frame
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

	.section .note.GNU-stack,"",%progbits
