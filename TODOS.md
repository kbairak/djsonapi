- [ ] Figure out how to elegantly support the bulk profile

- [ ] (Maybe) Use special decorators to highlight special classmethods instead
  of relying on the prefix of their name so that if a user writes a method like
  `get_queryset`, it won't be mistaken for a relationship

- [ ] Support for async views

- [x] Turn this into a proper python package (maybe opensource it, after
  figuring out what to do with the copy-pasted code from apiv3)

- [x] Tests!!! (somehow)

- [x] During relationship serialization, don't assume every dict is a plural
  relationship. Also, allow for plural relationships that have lists of
  resource identifiers

- [x] Support the 'fields' parameter

- [x] Map a classmethod to another, for example, map `/articles/1/categories/`
  to `/categories?filter[article]=1`
