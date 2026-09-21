import { json } from '@codemirror/lang-json'
import { markdown } from '@codemirror/lang-markdown'
import { python } from '@codemirror/lang-python'
import { xml } from '@codemirror/lang-xml'
import { EditorView } from '@codemirror/view'
import CodeMirror from '@uiw/react-codemirror'
import { useMemo } from 'react'

import { languageFor } from '@/lib/format'

export function TextPreview({
  file,
  content,
}: {
  file: { name: string; mime: string }
  content: string
}) {
  // CodeMirror throws on anything but a string; a parsed JSON body must never reach it
  const text = typeof content === 'string' ? content : JSON.stringify(content, null, 2)
  const extensions = useMemo(() => {
    const language = languageFor(file)
    const base = [EditorView.lineWrapping]
    if (language === 'xml') return [...base, xml()]
    if (language === 'json') return [...base, json()]
    if (language === 'markdown') return [...base, markdown()]
    if (language === 'python') return [...base, python()]
    return base
  }, [file])

  return (
    <CodeMirror
      value={text}
      height="100%"
      className="h-full text-[13px] [&_.cm-editor]:h-full [&_.cm-scroller]:overflow-auto"
      extensions={extensions}
      readOnly
      editable={false}
      basicSetup={{
        lineNumbers: true,
        foldGutter: true,
        highlightActiveLine: false,
        searchKeymap: true,
      }}
    />
  )
}
