/** Discovers real endpoint methods and evaluates their production Zod parsers. */
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve, dirname } from 'node:path'
import { runInNewContext } from 'node:vm'
import ts from 'typescript'
import { z } from 'zod'

const require = createRequire(import.meta.url)
const apiDir = resolve(
  dirname(new URL(import.meta.url).pathname),
  '../src/lib/data/api',
)
const normalize = (path) =>
  `/${path
    .replace(/^\//, '')
    .split('?')[0]
    .replace(/\{[^}]*\}/g, '{}')}`

function alternatives(node, tree) {
  if (ts.isStringLiteralLike(node)) return [node.text]
  if (ts.isConditionalExpression(node))
    return [
      ...alternatives(node.whenTrue, tree),
      ...alternatives(node.whenFalse, tree),
    ]
  if (ts.isIdentifier(node) && tree) {
    const declarations = []
    function find(candidate) {
      if (
        ts.isVariableDeclaration(candidate) &&
        candidate.name.getText(tree) === node.text &&
        candidate.initializer
      )
        declarations.push(candidate.initializer)
      ts.forEachChild(candidate, find)
    }
    find(tree)
    if (declarations.length === 1) return alternatives(declarations[0], tree)
  }
  if (ts.isTemplateExpression(node)) {
    let paths = [node.head.text]
    for (const span of node.templateSpans) {
      let values = ['{}']
      if (ts.isConditionalExpression(span.expression))
        values = alternatives(span.expression, tree)
      else if (ts.isIdentifier(span.expression)) {
        try {
          values = alternatives(span.expression, tree)
        } catch {
          /* Path parameters remain placeholders. */
        }
      }
      paths = paths.flatMap((path) =>
        values.map((value) => path + value + span.literal.text),
      )
    }
    return paths
  }
  throw new Error(`Unresolved API expression: ${node.getText()}`)
}

export function discoverContracts() {
  const cache = new Map()
  function load(filename) {
    if (cache.has(filename)) return cache.get(filename)
    const source = readFileSync(filename, 'utf8')
    const tree = ts.createSourceFile(
      filename,
      source,
      ts.ScriptTarget.Latest,
      true,
    )
    const entries = []
    let jsonTextDefinition
    function visit(node) {
      if (
        ts.isVariableDeclaration(node) &&
        node.name.getText(tree) === 'jsonText'
      )
        jsonTextDefinition = node.initializer
      if (
        ts.isCallExpression(node) &&
        node.expression.getText(tree) === 'phloApi'
      ) {
        const [path, parser, options] = node.arguments
        let schema = parser
        assert(path && schema, 'API call needs a response parser')
        const props =
          options && ts.isObjectLiteralExpression(options)
            ? options.properties
            : []
        const method = props.find((p) => p.name?.getText(tree) === 'method')
        const methods = method
          ? alternatives(method.initializer)
          : [
              props.some((p) => p.name?.getText(tree) === 'body')
                ? 'POST'
                : 'GET',
            ]
        const paths = alternatives(path, tree).map(normalize)
        let text = props.some(
          (p) =>
            p.name?.getText(tree) === 'responseType' &&
            p.initializer?.getText(tree) === "'text'",
        )
        if (
          ts.isCallExpression(schema) &&
          schema.expression.getText(tree) === 'jsonText'
        ) {
          assert(
            text && schema.arguments.length === 1,
            'Unexpected JSON-text parser call',
          )
          const helper = jsonTextDefinition
          assert(
            helper &&
              ts.isArrowFunction(helper) &&
              ts.isCallExpression(helper.body),
            'JSON-text helper changed',
          )
          const transform = helper.body.arguments[0]
          assert(
            helper.body.expression.getText(tree) === 'z.string().transform' &&
              ts.isArrowFunction(transform) &&
              ts.isBlock(transform.body),
            'JSON-text helper must decode a string',
          )
          const statement = transform.body.statements[0]
          assert(
            transform.body.statements.length === 1 &&
              ts.isTryStatement(statement) &&
              !statement.finallyBlock,
            'JSON-text helper has unchecked branches',
          )
          const accepted = statement.tryBlock.statements
          const rejected = statement.catchClause?.block.statements
          const schemaName = helper.parameters[0].name.getText(tree)
          const textName = transform.parameters[0].name.getText(tree)
          const contextName = transform.parameters[1].name.getText(tree)
          assert(
            accepted.length === 1 &&
              ts.isReturnStatement(accepted[0]) &&
              accepted[0].expression?.getText(tree).replace(/\s/g, '') ===
                `${schemaName}.parse(JSON.parse(${textName}))`,
            'JSON-text helper must apply its actual inner schema to parsed JSON',
          )
          const rejection = rejected?.[0].expression
          assert(
            rejected?.length === 2 &&
              rejection &&
              ts.isCallExpression(rejection) &&
              rejection.expression.getText(tree) ===
                `${contextName}.addIssue` &&
              rejection.arguments[0]?.properties.some(
                (p) =>
                  p.name?.getText(tree) === 'code' &&
                  p.initializer?.text === 'custom',
              ) &&
              ts.isReturnStatement(rejected[1]) &&
              rejected[1].expression?.getText(tree) === 'z.NEVER',
            'JSON-text helper must reject invalid JSON and schema failures',
          )
          // The real helper JSON-decodes text before applying this production parser.
          schema = schema.arguments[0]
          text = false
        }
        // Conditional path/method pairs use the same condition in saveQuery.
        for (const [i, route] of paths.entries()) {
          const verb = methods.length === paths.length ? methods[i] : methods[0]
          entries.push(
            `{path:${JSON.stringify(route)},method:${JSON.stringify(verb.toLowerCase())},text:${text},schema:${schema.getText(tree)},parser:${parser.getText(tree)}}`,
          )
        }
      }
      ts.forEachChild(node, visit)
    }
    visit(tree)
    const module = { exports: {} }
    cache.set(filename, module.exports)
    const code = ts.transpileModule(
      `${source}\nexport const __contracts = [${entries.join(',')}];`,
      {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          target: ts.ScriptTarget.ES2022,
        },
      },
    ).outputText
    const serverFn = () => {
      const builder = {
        inputValidator: () => builder,
        validator: () => builder,
        handler: (fn) => fn,
      }
      return builder
    }
    runInNewContext(
      code,
      {
        module,
        exports: module.exports,
        require: (name) => {
          if (name === '@tanstack/react-start')
            return { createServerFn: serverFn, createServerOnlyFn: (fn) => fn }
          if (name === '@tanstack/react-start/server')
            return { getRequestHeader: () => undefined }
          if (name.startsWith('.'))
            return load(resolve(dirname(filename), `${name}.ts`))
          return require(name)
        },
      },
      { filename },
    )
    return module.exports
  }
  return readdirSync(apiDir)
    .filter((f) => f.endsWith('.ts') && !f.endsWith('.test.ts'))
    .flatMap((f) =>
      load(resolve(apiDir, f)).__contracts.map((c) => ({ ...c, file: f })),
    )
}

function dereference(schema, document) {
  if (!schema.$ref) return schema
  return schema.$ref
    .split('/')
    .slice(1)
    .reduce((node, key) => node[key], document)
}

function compatible(api, client, document, location) {
  api = dereference(api, document)
  if (api.anyOf) {
    for (const option of api.anyOf)
      compatible(option, client, document, location)
    return
  }
  if (client.anyOf) {
    assert(
      client.anyOf.some((option) => {
        try {
          compatible(api, option, document, location)
          return true
        } catch {
          return false
        }
      }),
      `${location}: incompatible union`,
    )
    return
  }
  if (client.type) {
    const accepted = Array.isArray(client.type) ? client.type : [client.type]
    assert(
      accepted.includes(api.type) ||
        (api.type === 'integer' && accepted.includes('number')),
      `${location}: ${api.type} not ${client.type}`,
    )
  }
  if (client.enum)
    assert(
      api.enum?.every((value) => client.enum.includes(value)),
      `${location}: enum drift`,
    )
  if (client.const !== undefined)
    assert(
      api.const === client.const ||
        (api.enum?.length === 1 && api.enum[0] === client.const),
      `${location}: constant drift`,
    )
  for (const key of client.required ?? []) {
    assert(
      api.properties?.[key] && api.required?.includes(key),
      `${location}: API no longer requires ${key}`,
    )
  }
  for (const [key, value] of Object.entries(client.properties ?? {})) {
    if (api.properties?.[key])
      compatible(api.properties[key], value, document, `${location}.${key}`)
  }
  if (client.items)
    compatible(api.items ?? {}, client.items, document, `${location}[]`)
}

export function checkContract(document, contract) {
  const path = Object.keys(document.paths).find(
    (path) => normalize(path) === contract.path,
  )
  assert(path, `${contract.file}: API route missing ${contract.path}`)
  const operation = document.paths[path][contract.method]
  assert(
    operation,
    `${contract.file}: API method missing ${contract.method.toUpperCase()} ${path}`,
  )
  const successes = Object.entries(operation.responses).filter(([status]) =>
    /^2\d\d$/.test(status),
  )
  assert(successes.length, `${path}: no success response`)
  const client = z.toJSONSchema(contract.schema, {
    io: 'input',
    unrepresentable: 'any',
  })
  for (const [status, response] of successes) {
    if (contract.text) {
      const output = z.toJSONSchema(contract.schema, {
        io: 'output',
        unrepresentable: 'any',
      })
      assert(
        client.type === 'string' && output.type === 'string',
        `${path}: unrecognised text parser`,
      )
      if (status === '204') {
        assert(
          !response.content,
          `${path}: no-content response unexpectedly has a body`,
        )
        continue
      }
      const api = Object.values(response.content ?? {}).find(
        (media) => media.schema?.type === 'string',
      )?.schema
      assert(api, `${path}: API text success response has no string schema`)
      compatible(
        api,
        client,
        document,
        `${contract.method.toUpperCase()} ${path}`,
      )
      continue
    }
    const api = response.content?.['application/json']?.schema
    assert(
      api && Object.keys(api).length,
      `${path}: API success response has no schema`,
    )
    compatible(
      api,
      client,
      document,
      `${contract.method.toUpperCase()} ${path}`,
    )
  }
}
