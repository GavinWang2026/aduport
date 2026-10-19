import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { diffMarkdown, parseMarkdownBlocks, reviewMarkdown } from '../src/astDiff.ts'
import { parseRagElements } from '../src/ragBrowse.ts'

describe('AST markdown diff (v16 P2)', () => {
  it('keeps fence language and quote as blocks', () => {
    const blocks = parseMarkdownBlocks('# T\n\n> quote\n\n```js\nlet x=1;\n```\n\npara\n')
    assert.deepEqual(
      blocks.map((b) => b.kind),
      ['heading', 'blockquote', 'code', 'paragraph'],
    )
    assert.equal(blocks[2].lang, 'js')
    assert.equal(blocks[2].text, 'let x=1;')
  })

  it('reports a code block flattened to a paragraph', () => {
    const diff = diffMarkdown('# T\n\n```js\nlet x=1;\n```\n', '# T\n\n`let x=1;`\n')
    assert.equal(diff.approximateOnly, false)
    assert.equal(diff.equal, false)
    assert.equal(diff.changes[0].op, 'replace')
    assert.match(diff.changes[0].before, /^code\|/)
    assert.match(diff.changes[0].after, /^paragraph\|/)
  })

  it('treats identical structure as equal', () => {
    const src = '# T\n\n> quote\n\npara\n'
    const diff = diffMarkdown(src, src)
    assert.equal(diff.equal, true)
    assert.deepEqual(diff.changes, [])
  })

  it('lists added comments and insertions', () => {
    const left = 'para\n\n<!-- comment:ann "p1" old -->\n'
    const right = 'para\n\n<!-- comment:ann "p1" new -->\n\n++clause++ <!-- ins:ed -->\n'
    const review = reviewMarkdown(left, right)
    assert.ok(review)
    assert.equal(review.removed[0].text, 'old')
    assert.equal(review.added.some((note) => note.kind === 'comment' && note.text === 'new'), true)
    assert.equal(review.added.some((note) => note.kind === 'insert' && note.author === 'ed'), true)
    assert.equal(reviewMarkdown('para\n', 'para\n'), null)
  })

  it('reads rag element text for the browser', () => {
    const elements = parseRagElements(JSON.stringify({
      elements: [{ element_id: 'e1', type: 'NarrativeText', text: 'hello chunk' }],
    }))
    assert.equal(elements.length, 1)
    assert.equal(elements[0].id, 'e1')
    assert.equal(elements[0].text, 'hello chunk')
    assert.deepEqual(parseRagElements('# not json'), [])
  })
})
