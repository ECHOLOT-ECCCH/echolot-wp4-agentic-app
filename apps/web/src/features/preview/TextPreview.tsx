import { json } from '@codemirror/lang-json'
import { markdown } from '@codemirror/lang-markdown'
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
  const extensions = useMemo(() => {
    const language = languageFor(file)
    const base = [EditorView.lineWrapping]
    if (language === 'xml') return [...base, xml()]
    if (language === 'json') return [...base, json()]
    if (language === 'markdown') return [...base, markdown()]
    return base
  }, [file])

  return (
    <CodeMirror
      value={content}
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
