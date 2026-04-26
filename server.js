//"env": {
//    "CLAUDE_CODE_ATTRIBUTION_HEADER" : "0"
//  }

'use strict'
const express = require('express')
const { Readable } = require('stream')
const fs = require('fs')
const path = require('path')

// ─── Config ──────────────────────────────────────────────────────────────────
const TARGET_BASE    = 'https://openrouter.ai'
const TARGET_MODEL   = 'qwen/qwen3-coder-next'
const PORT           = process.env.PORT || 3000
const AUTH_TOKEN     = process.env.ANTHROPIC_AUTH_TOKEN

// ─── ANSI ────────────────────────────────────────────────────────────────────
const R  = '\x1b[0m'
const B  = '\x1b[1m'
const D  = '\x1b[2m'
const CY = '\x1b[36m'
const YL = '\x1b[33m'
const GR = '\x1b[32m'
const RE = '\x1b[31m'
const MG = '\x1b[35m'

let counter = 0

// ─── Detection & Translation ─────────────────────────────────────────────────
function detectClientTool(headers) {
  const ua = (headers['user-agent'] || '').toLowerCase()
  if (ua.includes('claude-code') || ua.includes('anthropic-sdk')) return 'Claude Code'
  if (ua.includes('opencode')) return 'OpenCode'
  // claude-code-router forwards with a plain 'node' user-agent
  if (ua === 'node') return 'Claude Code (router)'
  return `Unknown (${headers['user-agent'] || 'no-ua'})`
}

function detectFormat(path) {
  if (path.includes('/messages'))          return 'anthropic'
  if (path.includes('/chat/completions'))  return 'openai'
  return 'unknown'
}

function translateRequestBody(body) {
  if (!body || typeof body !== 'object') return body
  return { ...body, model: TARGET_MODEL }
}

function buildUpstreamHeaders(incoming) {
  const h = { 'content-type': 'application/json' }
  if (incoming['anthropic-version']) h['anthropic-version'] = incoming['anthropic-version']
  if (incoming['anthropic-beta'])    h['anthropic-beta']    = incoming['anthropic-beta']
  if (incoming['accept'])            h['accept']            = incoming['accept']
  h['authorization'] = `Bearer ${AUTH_TOKEN}`
  h['http-referer']  = `http://localhost:${PORT}`
  return h
}

function estimateTokens(body) {
  return Math.round(JSON.stringify(body || {}).length / 4)
}

// Returns an array of display lines for a single message entry
function getMessageLines(msg) {
  const lines = []
  const role  = msg.role || '?'

  if (role === 'system') {
    if (typeof msg.content === 'string') {
      const c = msg.content
      lines.push(`${D}(${c.length}c) "${text.slice(0, 200)}${c.length > 200 ? '…' : ''}"${R}`)
    } else if (Array.isArray(msg.content)) {
      for (const block of msg.content) {
        lines.push(`${D}(${block.text.length}c) "${block.text.slice(0, 200)}${block.text.length > 200 ? '…' : ''}"${R}`)
      }
    }
  }

  if (role === 'user') {
    if (typeof msg.content === 'string') {
      const c = msg.content
      lines.push(`${CY}(${c.length}c) "${c.slice(0, 300)}${c.length > 300 ? '…' : ''}"${R}`)
    } else if (Array.isArray(msg.content)) {
      for (const block of msg.content) {
        if (block.type === 'text') {
          lines.push(`${CY}(${block.text.length}c) "${block.text.slice(0, 300)}${block.text.length > 300 ? '…' : ''}"${R}`)
        } else if (block.type === 'tool_result') {
          const c = typeof block.content === 'string' ? block.content : JSON.stringify(block.content)
          lines.push(`${GR}✓ tool_result(${c.length}c): "${c.slice(0, 120)}${c.length > 120 ? '…' : ''}"${R}`)
        }
      }
    }
  }

  if (role === 'assistant') {
    // OpenAI tool_calls
    if (Array.isArray(msg.tool_calls) && msg.tool_calls.length > 0) {
      for (const tc of msg.tool_calls) {
        const args = tc.function?.arguments || '{}'
        lines.push(`${YL}⚙ tool_call: ${B}${tc.function?.name}${R}${YL}(${args.slice(0, 100)}${args.length > 100 ? '…' : ''})${R}`)
      }
    }
    // Text content (may coexist with tool_calls)
    if (typeof msg.content === 'string' && msg.content) {
      lines.push(`${MG}"${msg.content.slice(0, 150)}${msg.content.length > 150 ? '…' : ''}"${R}`)
    } else if (Array.isArray(msg.content)) {
      for (const block of msg.content) {
        if (block.type === 'text' && block.text) {
          lines.push(`${MG}"${block.text.slice(0, 150)}${block.text.length > 150 ? '…' : ''}"${R}`)
        } else if (block.type === 'tool_use') {
          // Anthropic format tool call
          const args = JSON.stringify(block.input || {})
          lines.push(`${YL}⚙ tool_call: ${B}${block.name}${R}${YL}(${args.slice(0, 100)}${args.length > 100 ? '…' : ''})${R}`)
        }
      }
    }
  }

  if (role === 'tool') {
    // OpenAI tool result
    const c = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)
    lines.push(`${GR}✓ result(${c.length}c): "${c.slice(0, 120)}${c.length > 120 ? '…' : ''}"${R}`)
  }

  return lines.length > 0 ? lines : [`${D}(empty)${R}`]
}

// ─── Request Printer ─────────────────────────────────────────────────────────
function printRequest(ctx, req, body, originalModel) {
  const { tool, format, id } = ctx
  const ts   = new Date().toISOString().replace('T', ' ').slice(0, 19)
  const tCol = tool.startsWith('Claude') ? MG : YL

  console.log()
  console.log(`${CY}╔${'═'.repeat(56)}╗${R}`)
  console.log(`${CY}║${R} ${B}${tCol}${tool}${R}  ${D}req#${id}${R}  ${D}${ts}${R}`)
  console.log(`${CY}╚${'═'.repeat(56)}╝${R}`)
  console.log()

  const fmtLabel = format === 'anthropic' ? 'Anthropic Messages API'
                 : format === 'openai'    ? 'OpenAI Chat Completions'
                 : format
  console.log(`${YL}▶ REQUEST${R}  ${D}${req.method} ${req.path}  ${fmtLabel}${R}`)
  console.log(`  Model        : ${RE}${originalModel}${R} → ${GR}${TARGET_MODEL}${R}`)
  console.log(`  Est. tokens  : ~${estimateTokens(body)}`)
  console.log()

  // Per-message breakdown
  const msgs = body.messages || []
  const ROLE_W = 9  // padEnd width for role label
  const roleColor = r => r === 'system' ? D : r === 'user' ? CY : r === 'assistant' ? MG : r === 'tool' ? GR : D
  const indent = '  '
  console.log(`${indent}${D}┌─ Messages (${msgs.length}) ${'─'.repeat(41)}${R}`)
  for (let i = 0; i < msgs.length; i++) {
    const msg  = msgs[i]
    const rc   = roleColor(msg.role)
    const rl   = (msg.role || '?').toUpperCase().padEnd(ROLE_W)
    const lines = getMessageLines(msg)
    console.log(`${indent}${D}│${R} [${i + 1}] ${rc}${B}${rl}${R} ${lines[0]}`)
    for (let j = 1; j < lines.length; j++) {
      console.log(`${indent}${D}│${R}      ${' '.repeat(ROLE_W + 2)}${lines[j]}`)
    }
  }
  console.log(`${indent}${D}└${'─'.repeat(54)}${R}`)
  console.log()

  // Tools list (collapsed — just count + names)
  const allTools = body.tools || body.functions || []
  if (allTools.length > 0) {
    const names = allTools.map(t => t.name || t.function?.name || '?').join(', ')
    console.log(`  ${B}Tools (${allTools.length}):${R} ${GR}${names}${R}`)
  } else {
    console.log(`  Tools        : ${D}none${R}`)
  }
  console.log()
}

// ─── Response Printers ───────────────────────────────────────────────────────
function printJsonResponse(ctx, body, status, startTime) {
  const ms   = Date.now() - startTime
  const sCol = status >= 200 && status < 300 ? GR : RE

  let usage = null
  if (body.usage) {
    if (body.usage.input_tokens != null)
      usage = `${body.usage.input_tokens} in / ${body.usage.output_tokens} out`
    else if (body.usage.prompt_tokens != null)
      usage = `${body.usage.prompt_tokens} in / ${body.usage.completion_tokens} out`
  }

  let preview = ''; let ctype = 'unknown'; const tcList = []
  if (Array.isArray(body.content)) {
    for (const block of body.content) {
      if (block.type === 'text')     { preview = preview || (block.text || '').slice(0, 200); ctype = 'text' }
      if (block.type === 'tool_use') {
        const args = JSON.stringify(block.input || {})
        tcList.push({ name: block.name, args })
        ctype = 'tool_call'
      }
    }
  } else if (body.choices) {
    const msg = body.choices[0]?.message
    if (msg?.content) { preview = msg.content.slice(0, 200); ctype = 'text' }
    if (msg?.tool_calls) {
      ctype = 'tool_call'
      for (const tc of msg.tool_calls) {
        tcList.push({ name: tc.function?.name || '?', args: tc.function?.arguments || '{}' })
      }
    }
  } else if (body.error) {
    preview = JSON.stringify(body.error).slice(0, 200); ctype = 'error'
  }

  console.log(`${GR}◀ RESPONSE${R}  ${D}+${ms}ms${R}`)
  console.log(`  Status       : ${sCol}${status}${R}`)
  console.log(`  Usage        : ${usage || `${D}unknown${R}`}`)
  console.log(`  Content type : ${CY}${ctype}${R}`)
  for (const tc of tcList) {
    const args = tc.args.slice(0, 160)
    console.log(`  Tool called  : ${YL}⚙ ${B}${tc.name}${R}${YL}(${args}${tc.args.length > 160 ? '…' : ''})${R}`)
  }
  if (preview) console.log(`  Preview      : ${D}"${preview.replace(/\n/g, '\\n')}${preview.length === 200 ? '…' : ''}"${R}`)
  console.log(`${D}${'─'.repeat(58)}${R}`)
  console.log()
}

function printStreamSummary(ctx, collected, startTime) {
  const ms = Date.now() - startTime
  let text = ''; let inTok = null; let outTok = null; let ctype = 'text'
  const toolCalls = {}  // index → { name, args }

  for (const line of collected.split('\n')) {
    if (!line.startsWith('data: ')) continue
    const raw = line.slice(6).trim()
    if (raw === '[DONE]') continue
    try {
      const e = JSON.parse(raw)
      // Anthropic SSE
      if (e.type === 'content_block_start' && e.content_block?.type === 'tool_use') {
        toolCalls[e.index] = { name: e.content_block.name, args: '' }
        ctype = 'tool_call'
      }
      if (e.type === 'content_block_delta') {
        if (e.delta?.type === 'text_delta') text += e.delta.text || ''
        if (e.delta?.type === 'input_json_delta') {
          if (toolCalls[e.index]) toolCalls[e.index].args += e.delta.partial_json || ''
          ctype = 'tool_call'
        }
      }
      if (e.type === 'message_start' && e.message?.usage) inTok  = e.message.usage.input_tokens
      if (e.type === 'message_delta' && e.usage)          outTok = e.usage.output_tokens
      // OpenAI SSE
      if (e.choices) {
        const d = e.choices[0]?.delta
        if (d?.content) text += d.content
        if (d?.tool_calls) {
          ctype = 'tool_call'
          for (const tc of d.tool_calls) {
            if (!toolCalls[tc.index]) toolCalls[tc.index] = { name: '', args: '' }
            if (tc.function?.name)      toolCalls[tc.index].name += tc.function.name
            if (tc.function?.arguments) toolCalls[tc.index].args += tc.function.arguments
          }
        }
      }
      if (e.usage?.prompt_tokens)     inTok  = e.usage.prompt_tokens
      if (e.usage?.completion_tokens) outTok = e.usage.completion_tokens
    } catch { /* skip malformed chunks */ }
  }

  const usage  = inTok != null && outTok != null ? `${inTok} in / ${outTok} out` : `${D}unknown${R}`
  const tcList = Object.values(toolCalls)
  const preview = text.slice(0, 200)

  console.log(`${GR}◀ RESPONSE (streaming)${R}  ${D}+${ms}ms${R}`)
  console.log(`  Status       : ${GR}200${R}`)
  console.log(`  Usage        : ${usage}`)
  console.log(`  Content type : ${CY}${ctype}${R}`)
  for (const tc of tcList) {
    const args = tc.args.slice(0, 160)
    console.log(`  Tool called  : ${YL}⚙ ${B}${tc.name}${R}${YL}(${args}${tc.args.length > 160 ? '…' : ''})${R}`)
  }
  if (preview) console.log(`  Preview      : ${D}"${preview.replace(/\n/g, '\\n')}${preview.length === 200 ? '…' : ''}"${R}`)
  console.log(`${D}${'─'.repeat(58)}${R}`)
  console.log()
}

// ─── Handlers ────────────────────────────────────────────────────────────────
async function handleStreamingResponse(upstream, res, ctx, startTime) {
  res.setHeader('Content-Type', 'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection', 'keep-alive')
  const reqId = upstream.headers.get('x-request-id')
  if (reqId) res.setHeader('x-request-id', reqId)

  let collected = ''
  process.stdout.write(`  ${D}streaming${R} `)

  const nodeStream = Readable.fromWeb(upstream.body)
  await new Promise((resolve, reject) => {
    nodeStream.on('data', chunk => {
      res.write(chunk)
      collected += chunk.toString()
      process.stdout.write('.')
    })
    nodeStream.on('end',   () => { res.end(); process.stdout.write('\n'); resolve() })
    nodeStream.on('error', reject)
  })

  printStreamSummary(ctx, collected, startTime)
}

async function handleJsonResponse(upstream, res, ctx, startTime) {
  const text = await upstream.text()
  let body
  try   { body = JSON.parse(text) }
  catch { body = { raw: text }    }
  res.status(upstream.status).json(body)
  printJsonResponse(ctx, body, upstream.status, startTime)
}

// ─── App ─────────────────────────────────────────────────────────────────────
const app = express()
app.use(express.json({ limit: '10mb' }))

app.get('/health', (_req, res) => {
  res.json({ ok: true, target: TARGET_BASE, model: TARGET_MODEL, requests: counter })
})

app.all('/*', async (req, res) => {
  const startTime     = Date.now()
  const id            = ++counter
  const tool          = detectClientTool(req.headers)
  const format        = detectFormat(req.path)
  const originalModel = req.body?.model || 'unknown'
  const translated    = translateRequestBody(req.body)

  printRequest({ tool, format, id }, req, translated, originalModel)

  const qs          = req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''
  const upstreamUrl = TARGET_BASE + req.path + qs

  let upstream
  try {
    upstream = await fetch(upstreamUrl, {
      method:  req.method,
      headers: buildUpstreamHeaders(req.headers),
      ...(req.method !== 'GET' && req.method !== 'HEAD' && { body: JSON.stringify(translated) })
    })
  } catch (err) {
    console.error(`${RE}Upstream error: ${err.message}${R}`)
    return res.status(502).json({ error: 'upstream_unreachable', message: err.message })
  }

  const isStreaming = req.body?.stream === true
    || upstream.headers.get('content-type')?.includes('text/event-stream')

  try {
    if (isStreaming) await handleStreamingResponse(upstream, res, { tool, format, id }, startTime)
    else             await handleJsonResponse(upstream, res, { tool, format, id }, startTime)
  } catch (err) {
    console.error(`${RE}Handler error: ${err.message}${R}`)
    if (!res.headersSent) res.status(500).json({ error: err.message })
  }
})

// Silent passthrough for other paths (e.g. /v1/models)
// app.all('*', async (req, res) => {
//   const upstreamUrl = TARGET_BASE + req.path
//   console.log(`${D}passthrough ${req.method} ${req.path}${R}`)
//   try {
//     const upstream = await fetch(upstreamUrl, {
//       method:  req.method,
//       headers: buildUpstreamHeaders(req.headers),
//       ...(req.method !== 'GET' && req.method !== 'HEAD' && req.body && { body: JSON.stringify(req.body) })
//     })
//     const body = await upstream.json()
//     res.status(upstream.status).json(body)
//   } catch (err) {
//     res.status(502).json({ error: err.message })
//   }
// })

app.use((err, _req, res, _next) => {
  console.error(`${RE}Express error: ${err.message}${R}`)
  res.status(500).json({ error: err.message })
})

app.listen(PORT, () => {
  console.log()
  console.log(`${B}${CY}  OpenRouter Logging Proxy  ${R}`)
  console.log(`${GR}  Listening on :${PORT}${R}`)
  console.log(`  Forwarding → ${TARGET_BASE}`)
  console.log(`  Model:        ${B}${TARGET_MODEL}${R}`)
  if (!AUTH_TOKEN) console.warn(`${RE}  WARNING: ANTHROPIC_AUTH_TOKEN is not set!${R}`)
  console.log()
})
