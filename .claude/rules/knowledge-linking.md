## Obsidian Knowledge Linking

When creating or updating persistent knowledge:

1. SEARCH BEFORE CREATING
   - Search Graphify and the Obsidian vault for related concepts first.
   - Reuse an existing note when it represents the same concept.
   - Do not create duplicate notes under slightly different names.

2. CREATE MEANINGFUL LINKS
   - Use [[wiki links]] to connect genuinely related concepts.
   - Link important systems, components, decisions, tools, people,
     processes, dependencies, and projects.
   - Prefer 3–8 strong links over many weak links.
   - Do not link concepts merely because they appear in the same text.

3. USE DESCRIPTIVE RELATIONSHIPS
   Do not dump links into an unexplained "Related" list when the
   relationship can be expressed naturally.

   Prefer:
   "The [[Video Pipeline]] sends rendering jobs to [[ComfyUI]]."

   Instead of:
   "Related: [[Video Pipeline]], [[ComfyUI]]"

4. CONNECT NEW KNOWLEDGE TO OLD KNOWLEDGE
   - New notes should rarely become isolated nodes.
   - Search for existing parent concepts, dependencies, implementations,
     decisions, and related systems.
   - Link the new knowledge to the most relevant existing notes.

5. PRESERVE KNOWLEDGE OWNERSHIP
   - Determine whether a file is generated/owned by Graphify before
     manually modifying it.
   - Do not manually maintain relationships inside Graphify-generated
     files that will be overwritten on regeneration.
   - Put durable human/AI-authored knowledge in persistent notes that
     Graphify does not own.

6. USE GRAPHIFY FIRST FOR CODE KNOWLEDGE
   - For questions about code architecture, dependencies, functions,
     classes, modules, or implementation relationships, query Graphify
     before performing broad filesystem searches.
   - Treat Graphify as the primary structural map of the codebase.
   - Use Obsidian for durable semantic/project knowledge that extends
     beyond relationships automatically extracted from code.

7. MAINTAIN KNOWLEDGE QUALITY
   - Merge duplicate concepts when discovered.
   - Prefer canonical note names.
   - Do not create links solely to make the graph denser.
   - Broken or obsolete relationships should be corrected when noticed.
   - A smaller number of meaningful edges is better than a dense,
     noisy graph.

8. WHEN SAVING IMPORTANT NEW KNOWLEDGE
   Follow this sequence:

   Search existing knowledge
        ↓
   Identify canonical concepts
        ↓
   Decide whether a new note is necessary
        ↓
   Create/update note
        ↓
   Add meaningful [[wiki links]]
        ↓
   Check for duplicates/conflicts
        ↓
   Preserve Graphify-owned generated content
