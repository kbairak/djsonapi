import { Collection } from "./collection.js"
import { excClassFor } from "./exceptions.js"
import { Resource } from "./resource.js"
import type { Document, ResourceObject } from "./types.js"

export interface SdkConfig {
  host?: string
  headers?: () => Promise<Record<string, string>>
}

export type ResourceConstructor<T extends Resource = Resource> = typeof Resource & {
  new (...args: ConstructorParameters<typeof Resource>): T
  _type: string
  _sdk: DjsonApiSdk
}

export interface RequestOptions {
  method?: string
  body?: unknown
  params?: Record<string, string>
  signal?: AbortSignal
}

export class DjsonApiSdk {
  host = ""
  headers: () => Promise<Record<string, string>> = async () => ({})
  debug = false
  private _registry = new Map<string, ResourceConstructor>()

  constructor(config?: SdkConfig) {
    if (config) {
      this.host = config.host ?? ""
      this.headers = config.headers ?? (async () => ({}))
    }
  }

  _log(...args: unknown[]): void {
    if (this.debug) console.log("[djsonapi]", ...args)
  }

  setup(config: SdkConfig): void {
    if (config.host !== undefined) this.host = config.host
    if (config.headers !== undefined) this.headers = config.headers
  }

  static _withProxy(sdk: DjsonApiSdk): DjsonApiSdk {
    return new Proxy(sdk, {
      get(target, prop, receiver) {
        if (prop in target || typeof prop === "symbol") {
          return Reflect.get(target, prop, receiver)
        }
        return target._getResourceClass(prop as string)
      },
    })
  }

  static create(config: SdkConfig): DjsonApiSdk {
    return DjsonApiSdk._withProxy(new DjsonApiSdk(config))
  }

  _getResourceClass(name: string): ResourceConstructor {
    if (!this._registry.has(name)) {
      const cls = class extends Resource {} as unknown as ResourceConstructor
      cls._type = name
      cls._sdk = this
      this._registry.set(name, cls)
    }
    return this._registry.get(name)!
  }

  async _request(
    path: string,
    opts: RequestOptions = {},
  ): Promise<Document | null> {
    const url = new URL(path, this.host)
    if (opts.params) {
      url.search = new URLSearchParams(opts.params).toString()
    }
    const fullUrl = url.toString()
    this._log(`${opts.method ?? "GET"} ${fullUrl}`)
    if (opts.body) this._log("body:", opts.body)
    const res = await fetch(fullUrl, {
      method: opts.method ?? "GET",
      headers: {
        ...(await this.headers()),
        "content-type": "application/vnd.api+json",
        accept: "application/vnd.api+json",
      },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      signal: opts.signal,
    })
    const body: Document | null =
      res.status === 204 ? null : await res.json()
    this._log("response", res.status, body)
    if (!res.ok) {
      this._raiseForStatus(res.status, (body ?? {}) as Record<string, unknown>)
    }
    return body
  }

  _raiseForStatus(status: number, body: Record<string, unknown>): void {
    if (status < 400) return
    const errors = (body as Record<string, unknown>).errors
    if (Array.isArray(errors)) {
      const excs = errors.map(
        (e: Record<string, unknown>) =>
          new (excClassFor(Number(e.status) || status))(
            Number(e.status) || status,
            String(e.title ?? ""),
            String(e.detail ?? ""),
          ),
      )
      if (excs.length === 1) throw excs[0]
      throw new AggregateError(excs, "JSON:API error(s)")
    }
    const Exc = excClassFor(status)
    throw new Exc(status, String(body.title ?? ""), String(body.detail ?? ""))
  }

  _createResource(data: ResourceObject): Resource {
    const cls = this._getResourceClass(data.type)
    return new cls({ _data: data })
  }

  _parseResponse(response: Document): Resource | Resource[] {
    const resources = new Map<string, Resource>()
    const data = response.data
    const self = this

    function add(item: ResourceObject) {
      const r = self._createResource(item)
      resources.set(`${(r.constructor as typeof Resource)._type}:${item.id}`, r)
    }

    if (Array.isArray(data)) {
      for (const item of data) add(item)
    } else {
      add(data)
    }

    for (const item of response.included ?? []) {
      const key = `${item.type}:${item.id}`
      if (!resources.has(key)) {
        resources.set(key, this._createResource(item))
      }
    }

    for (const resource of resources.values()) {
      for (const [relName, related] of Object.entries(resource._related)) {
        if (related instanceof Resource && related.id != null) {
          const key = `${(related.constructor as typeof Resource)._type}:${related.id}`
          const resolved = resources.get(key)
          if (resolved) resource._related[relName] = resolved
        } else if (related instanceof Collection && related._data != null) {
          for (let i = 0; i < related._data.length; i++) {
            const item = related._data[i]
            if (item.id != null) {
              const key = `${(item.constructor as typeof Resource)._type}:${item.id}`
              const resolved = resources.get(key)
              if (resolved) related._data[i] = resolved
            }
          }
        }
      }
    }

    const meta = (response.meta ?? {}) as Record<string, unknown>
    if (Array.isArray(data)) {
      const result = data.map(
        (item) => resources.get(`${item.type}:${item.id}`)!,
      )
      for (const r of result) r.meta = meta
      return result
    }
    const result = resources.get(`${data.type}:${data.id}`)!
    result.meta = meta
    return result
  }
}

export const sdk: DjsonApiSdk = DjsonApiSdk._withProxy(new DjsonApiSdk())
