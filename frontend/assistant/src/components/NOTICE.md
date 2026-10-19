# Third-party components

`components/assistant-ui/`, `components/ui/`, `hooks/`, and `lib/utils.ts`
started as the assistant-ui / shadcn copy-in components. They have since been
rewritten to CC's styling standard (`docs/development/ccs.md`) and are kept by
hand: do not regenerate them with the assistant-ui or shadcn command. A
component copied in from there is first rewritten to the standard (`cc-*`
classes and `globals.css` variables) before it goes into `src/`.

They are distributed under the MIT license:

```
MIT License

Copyright (c) 2025 AgentbaseAI Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The quickstart's own code (`app/`, `lib/`, `setup/`, `components/tool-uis/`)
follows the license at the repository root.
