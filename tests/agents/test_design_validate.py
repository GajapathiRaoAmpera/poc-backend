import pytest

from app.modules.agents.design.schema import DesignAgentOutput
from app.modules.agents.design.validate import design_issues, html_issues

OK = "<html><body><a href='#S-002'>Next</a><img src='data:image/png;base64,AAAA'></body></html>"


def codes(html):
    return {i.code for i in html_issues(html)}


def test_html_rejects_script_iframe_object_embed():
    for tag in ("<script>1</script>", "<iframe></iframe>", "<object></object>", "<embed>"):
        assert codes(f"<body>{tag}</body>") == {"HTML_UNSAFE"}
    assert html_issues(OK) == []


def test_html_rejects_on_handlers():
    assert codes("<button onclick='x()'>Go</button>") == {"HTML_UNSAFE"}


def test_html_rejects_external_css_url():
    for bad in (
        '<img src="https://x">',
        '<link href="http://x">',
        '<div style="background:url(https://x)"></div>',
        "<style>@import 'x.css';</style>",
        "<style>a{background:url('//x/y.png')}</style>",
    ):
        assert codes(bad) == {"HTML_UNSAFE"}, bad
    assert html_issues('<div style="background:url(data:image/png;base64,AA)"></div>') == []


def _out(reqs_on_screen, flows=()):
    return DesignAgentOutput.model_validate(
        {
            "summary": "s",
            "theme": dict(
                primary="#000", surface="#fff", text="#111", font_family="x", radius="8px"
            ),
            "screens": [
                {
                    "id": "S-001",
                    "name": "A",
                    "purpose": "p",
                    "requirement_ids": reqs_on_screen,
                    "html": OK,
                }
            ],
            "flows": list(flows),
        }
    )


REQS = {"FR-001": "MUST", "FR-002": "SHOULD", "NFR-001": "MUST"}


def test_must_requirement_uncovered():
    issues = design_issues(_out(["FR-002"]), REQS)
    assert [i.code for i in issues] == ["MUST_UNCOVERED"]
    assert "FR-001" in issues[0].message and "NFR" not in issues[0].message
    assert design_issues(_out(["FR-001"]), REQS) == []


def test_unknown_requirement_and_flow():
    flow = {"from_screen": "S-001", "action": "Go", "to_screen": "S-009"}
    got = {i.code for i in design_issues(_out(["FR-001", "FR-099"], [flow]), REQS)}
    assert got == {"REQ_UNKNOWN", "FLOW_UNKNOWN"}


@pytest.mark.parametrize(
    "bad",
    [
        "<!--><script>alert(1)</script>-->",
        "<!---><script>alert(1)</script>-->",
        "<![CDATA[><script>alert(1)</script>]]>",
        "<!-- c --><p>x</p>",
        "<?xml version='1.0'?><p>x</p>",
        "<SCRIPT>1</SCRIPT>",
        '<div style="background:u\\72l(https://x)"></div>',
        '<div style="background:\\75rl(https://x)"></div>',
        "<style>@\\69mport 'https://x';</style>",
        "<div style=\"background:image-set('https://x' 1x)\"></div>",
        "<div style=\"background:-webkit-image-set('//x' 1x)\"></div>",
        '<div style="width:expression(alert(1))"></div>',
        '<meta http-equiv=refresh content="0;url=https://evil">',
        '<svg><a><set attributeName="href" to="javascript:alert(1)"/></a></svg>',
        '<svg><animate attributeName="href" values="javascript:alert(1)"/></svg>',
        '<a href="data:text/html,<script>alert(1)</script>">x</a>',
        '<a href="java\tscript:alert(1)">x</a>',
        '<img srcset="data:a 1x, https://x 2x">',
        '<body background="https://x">',
        '<img lowsrc="https://x">',
        '<img src="data:image/svg+xml;base64,AAAA">',
        '<img src="https://x">',
        '<form action="https://x"></form>',
        '<button formaction="https://x">Go</button>',
        "<template><p>x</p></template>",
    ],
)
def test_html_probe_payloads_rejected(bad):
    assert codes(bad) == {"HTML_UNSAFE"}


def test_html_allows_plain_mockup():
    html = (
        "<!DOCTYPE html><html><head><title>t</title><style>a{color:#111}"
        ".x{background:url(data:image/png;base64,AAAA)}</style></head>"
        '<body><nav><a href="#S-002" class="x" aria-label="n">Go</a></nav>'
        '<input type="text" placeholder="Name" required></body></html>'
    )
    assert html_issues(html) == []
