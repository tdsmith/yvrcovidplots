import datetime as dt
import io
import warnings
from pathlib import Path
from typing import Optional

import attr
import dateutil.parser
import dateutil.tz
import matplotlib as mpl  # type: ignore
import numpy as np
import pandas as pd  # type: ignore
import plotnine as gg  # type: ignore
import requests
import toml
import typer
from glom import glom  # type: ignore
from mastodon import Mastodon
from PIL import Image, ImageDraw, ImageFont  # type: ignore
from TwitterAPI import TwitterAPI  # type: ignore

TZ = dateutil.tz.gettz("America/Vancouver")


@attr.define
class WastewaterData:
    data: pd.DataFrame
    last_updated: dt.datetime


def get_data() -> WastewaterData:
    ctx = requests.post(
        "http://www.metrovancouver.org/services/liquid-waste/environmental-management/covid-19-wastewater/_api/contextinfo",
        headers={"Accept": "application/json;odata=verbose"},
    ).json()
    digest = glom(ctx, "d.GetContextWebInformation.FormDigestValue")

    def get(url):
        return requests.get(
            url,
            headers={
                "Accept": "application/json;odata=verbose",
                "X-RequestDigest": digest,
            },
            params={
                "$select": ["CalculatedDate", "Plant", "Value", "DailyLoad", "Note"]
            },
        ).json()

    last = get(
        "http://www.metrovancouver.org/services/liquid-waste/environmental-management/covid-19-wastewater/_api/lists/getbytitle('WastewaterCOVIDData')/items",
    )

    rows = []
    while True:
        rows.extend(last["d"]["results"])
        url = last["d"].get("__next")
        if not url:
            break
        last = get(url)

    l = requests.get(
        "http://www.metrovancouver.org/services/liquid-waste/environmental-management/covid-19-wastewater/_api/lists/getbytitle('WastewaterCOVIDData')",
        headers={"Accept": "application/json;odata=verbose", "X-RequestDigest": digest},
    ).json()

    last_updated = glom(l, ("d.LastItemModifiedDate", dateutil.parser.isoparse))
    last_updated = last_updated.astimezone(TZ)

    df = pd.DataFrame(rows).drop(columns=["__metadata"])
    df = df[df.Note != "No sample collected"]
    df.CalculatedDate = pd.to_datetime(df.CalculatedDate)
    df.Plant.replace(
        {
            "Iona Island": "Iona Island WWTP (Vancouver)",
            "Annacis Island": "Annacis Island WWTP (Fraser area)",
            "Lulu Island": "Lulu Island WWTP (Richmond)",
            "Lions Gate": "Lions Gate WWTP (North Shore)",
            "Northwest Langley": "Northwest Langley WWTP",
        },
        inplace=True,
    )

    return WastewaterData(df, last_updated)


def font(size: int) -> ImageFont:
    fonts = (
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    )
    for f in fonts:
        try:
            return ImageFont.truetype(f, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def render_plot(data: WastewaterData) -> Image:
    df = data.data

    gg.options.figure_size = (8, 8)

    all_time_plot = (
        gg.ggplot(
            gg.aes(
                "CalculatedDate",
                "DailyLoad/1e9",
                group=0,
            ),
            df,
        )
        + gg.geom_point(size=1)
        + gg.geom_smooth(span=0.1, alpha=0.3, size=0.2, se=None, method="loess")
        + gg.scale_x_date(limits=(df.CalculatedDate.min(), dt.datetime.now(tz=TZ)))
        + gg.geom_vline(xintercept=dt.date.today(), alpha=0.4)
        + gg.facet_wrap("~Plant", scales="free_y", ncol=1)
        + gg.labs(x="Date", y="COVID-19 copies/day / 1e9", title="All time")
        + gg.theme_bw()
        + gg.theme(
            axis_text_x=gg.element_text(angle=30, hjust=1),
        )
    )
    all_time_plot

    recent_data = df[
        df.CalculatedDate >= df.CalculatedDate.max() - dt.timedelta(days=60)
    ]

    recent_plot = (
        gg.ggplot(
            gg.aes(
                "CalculatedDate",
                "DailyLoad/1e9",
                group=0,
            ),
            recent_data,
        )
        + gg.geom_smooth(alpha=0.3, se=False, fill=None, size=0.3, span=0.5)
        + gg.geom_point()
        + gg.scale_x_date(
            limits=(recent_data.CalculatedDate.min(), dt.datetime.now(tz=TZ))
        )
        + gg.ylim(0, None)
        + gg.geom_vline(xintercept=dt.date.today(), alpha=0.4)
        + gg.theme_bw()
        + gg.theme(
            axis_text_x=gg.element_text(angle=30, hjust=1),
        )
        + gg.facet_wrap("~Plant", scales="free_y", ncol=1)
        + gg.labs(x="Date", y="COVID-19 copies/day / 1e9", title="Last 60 days")
    )
    recent_plot

    recent_fig = recent_plot.draw()
    recent_fig.dpi = 300
    recent_fig.draw(recent_fig.canvas.get_renderer())
    recent_ar = np.asarray(recent_fig.canvas.buffer_rgba())

    all_time_fig = all_time_plot.draw()
    all_time_fig.dpi = 300
    all_time_fig.draw(all_time_fig.canvas.get_renderer())
    all_time_ar = np.asarray(all_time_fig.canvas.buffer_rgba())

    panel = Image.fromarray(np.hstack([all_time_ar, recent_ar]))

    figure = Image.new("RGBA", (4800, 2600), color="white")
    figure.paste(panel, (0, 50))

    draw = ImageDraw.Draw(figure)

    now = dt.datetime.now(tz=TZ)
    last_updated = data.last_updated.astimezone(TZ)

    def fmt(ts: dt.datetime) -> str:
        month = ts.strftime("%b")
        hour = (ts.hour % 12) or 12
        tail = ts.strftime(":%M %p")
        return f"{month} {ts.day}, {ts.year} {hour}{tail}"

    draw.text(
        (50, 50),
        "@wastewater@tds.xyz Metro Vancouver Wastewater COVID-19 Summary",
        font=font(72),
        fill="black",
    )
    draw.text(
        (20, 2500),
        f"Plot generated {fmt(now)}. Data last updated {fmt(last_updated)}. Data courtesy Metro Vancouver. Follow me at https://mastodon.tds.xyz/@wastewater or @YVRCovidPlots on Twitter.",
        font=font(36),
        fill="black",
    )

    return figure


def do_tweet(secrets: dict, text: str, media: io.BufferedIOBase) -> str:
    api = TwitterAPI(**secrets)
    upload = api.request("media/upload", None, dict(media=media.read()))
    upload.response.raise_for_status()
    media_id = str(upload.json()["media_id"])

    api2 = TwitterAPI(api_version="2", **secrets)
    post = api2.request(
        "tweets",
        dict(
            text=text,
            media={"media_ids": [media_id]},
        ),
        method_override="POST",
    )
    post.response.raise_for_status()
    return glom(post.json(), "data.id")


def do_toot(secrets: dict, text: str, media: io.BufferedIOBase) -> str:
    api = Mastodon(**secrets)
    upload = api.media_post(media.read(), mime_type="image/png")
    toot = api.status_post(text, media_ids=[upload["id"]])
    return toot["url"]


def main(
    save_plot: bool = False,
    tweet: bool = False,
    toot: bool = False,
    dump_csv: bool = False,
    last_run_file: Optional[Path] = None,
):
    mpl.use("agg")
    warnings.filterwarnings("ignore", category=FutureWarning)

    secrets = toml.load("secrets.toml")

    data = get_data()

    if dump_csv:
        data.data.to_csv("wastewater.csv", index=False)

    if last_run_file:
        try:
            last_run = dt.datetime.fromisoformat(last_run_file.read_text())
            if last_run >= data.last_updated:
                return
            now = dt.datetime.now(tz=TZ)
            if (now - data.last_updated) < dt.timedelta(minutes=30):
                # let the data settle before tweeting
                return
        except Exception:
            pass

    figure = render_plot(data)

    if save_plot:
        figure.save("image.png", dpi=(300, 300))

    updated_month = data.last_updated.strftime("%B")
    updated_str = f"{updated_month} {data.last_updated.day}"
    last_test = data.data["CalculatedDate"].max()
    last_test_month = last_test.strftime("%B")
    last_test_str = f"{last_test_month} {last_test.day}"

    text = f"Metro Vancouver wastewater COVID surveillance data was published {updated_str}. The most recent test was {last_test_str}."

    figure_buffer = io.BytesIO()
    figure.save(figure_buffer, "png", dpi=(300, 300))

    responses = []

    if tweet:
        figure_buffer.seek(0)
        responses.append(
            do_tweet(secrets["twitter"], text + " @CovidPoops19", figure_buffer)
        )

    if toot:
        figure_buffer.seek(0)
        responses.append(
            do_toot(
                secrets["mastodon"],
                text + " #covid #covid19 #wastewater #vancouver #yvr",
                figure_buffer,
            )
        )

    if (tweet or toot) and last_run_file:
        last_run_file.write_text(data.last_updated.isoformat())

    if responses:
        print("\n".join(responses))


if __name__ == "__main__":
    typer.run(main)
