-module(export_demo).
-export([
 public/0, % export comment
 'quoted-name'/0
]).
public() -> ok.
'quoted-name'() -> ok.
private() -> ok.
