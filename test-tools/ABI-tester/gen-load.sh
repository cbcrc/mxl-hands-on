#!/usr/bin/env bash
# Usage: ./gen-load.sh <lanes> <times> > /tmp/load.json
# <times> is *additional* passes over s5/s6, so grains committed = times + 1.
set -euo pipefail
jq -n --argjson lanes "${1:-8}" --argjson times "${2:-1799}" '
def letter($n):
  def go: if .n < 0 then .s
          else {n: ((.n / 26 | floor) - 1), s: (([65 + (.n % 26)] | implode) + .s)} | go end;
  {n:$n, s:""} | go;
def flowid($i): "a1b2c3d4-0002-4000-8000-" + ("000000000000\($i)" | .[-12:]);
def flowdef($i):
  { id: flowid($i),
    label: "ABI Tester Load \(letter($i))",
    description: "1080p29.97 v210 load lane \(letter($i))",
    format: "urn:x-nmos:format:video",
    media_type: "video/v210",
    tags: { "urn:x-nmos:tag:grouphint/v1.0": ["ABI-Tester-Load-\(letter($i)):Video"] },
    parents: [],
    grain_rate: { numerator: 30000, denominator: 1001 },
    frame_width: 1920, frame_height: 1080,
    interlace_mode: "progressive", colorspace: "BT709",
    components: [ {name:"Y", width:1920,height:1080, bit_depth:10},
                  {name:"Cb",width:960, height:1080, bit_depth:10},
                  {name:"Cr",width:960, height:1080, bit_depth:10} ]
  } | tojson;
def lane($i): letter($i) as $L |
  [ {id:"s1", call:"mxlCreateFlowWriter",
              args:{instance:"I", flow_def:flowdef($i)}, out:{writer:"W\($L)"}},
    {id:"s2", call:"setCursor",
              args:{index:{mode:"current", edit_rate:{num:30000, den:1001}}}},
    {id:"s3", call:"mxlFlowWriterOpenGrain", args:{writer:"W\($L)", index:{mode:"cursor"}},
              fill:{mode:"none"}, out:{grain:"g\($L)"},
              note:"warm-up: first OpenGrain in a process is an outlier"},
    {id:"s4", call:"mxlFlowWriterCancelGrain", args:{writer:"W\($L)", grain:"g\($L)"}},
    {id:"s5", call:"mxlFlowWriterOpenGrain", args:{writer:"W\($L)", index:{mode:"cursor"}},
              pace:{jitter_ms:2}, fill:{mode:"const", byte:(16+($i%30)*8), stamp:true},
              out:{grain:"g\($L)"}},
    {id:"s6", call:"mxlFlowWriterCommitGrain", args:{writer:"W\($L)", grain:"g\($L)"},
              advance_cursor:1},
    {id:"s7", call:"repeat", args:{to:"s5", times:$times}} ];
{ name: "load-\($lanes)-lane",
  description: "\($lanes) lane(s) writing 1080p29.97 v210, paced to each grain OTS with +/-2 ms jitter",
  lanes: ( [range(0;$lanes)]
           | map({ key: letter(.), value: lane(.) })
           | from_entries ) }
'