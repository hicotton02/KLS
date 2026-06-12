from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass

from app.db import init_db
from app.settings import get_settings
from app.sync_service import (
    retag_bills,
    sync_alabama,
    sync_alaska,
    sync_arkansas,
    sync_arizona,
    sync_california,
    sync_connecticut,
    sync_colorado,
    sync_delaware,
    sync_district_of_columbia,
    sync_florida,
    sync_federal,
    sync_georgia,
    sync_hawaii,
    sync_illinois,
    sync_indiana,
    sync_idaho,
    sync_iowa,
    sync_kansas,
    sync_kentucky,
    sync_louisiana,
    sync_maine,
    sync_maryland,
    sync_massachusetts,
    sync_michigan,
    sync_minnesota,
    sync_mississippi,
    sync_missouri,
    sync_montana,
    sync_nebraska,
    sync_nevada,
    sync_new_hampshire,
    sync_new_jersey,
    sync_new_york,
    sync_north_carolina,
    sync_north_dakota,
    sync_new_mexico,
    sync_oklahoma,
    sync_ohio,
    sync_oregon,
    sync_pennsylvania,
    sync_rhode_island,
    sync_south_carolina,
    sync_south_dakota,
    sync_tennessee,
    sync_texas,
    sync_utah,
    sync_vermont,
    sync_virginia,
    sync_washington,
    sync_west_virginia,
    sync_wisconsin,
    sync_wyoming,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Keeping Law Simple maintenance commands")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create the local database schema")

    sync_parser = subparsers.add_parser("sync", help="Sync supported state and federal bills into the local database")
    sync_parser.add_argument("--alaska-years", help="Comma-separated list of Alaska legislative years")
    sync_parser.add_argument("--kansas-years", help="Comma-separated list of Kansas legislative years")
    sync_parser.add_argument("--years", help="Comma-separated list of Wyoming legislative years")
    sync_parser.add_argument("--alabama-years", help="Comma-separated list of Alabama legislative years")
    sync_parser.add_argument("--arizona-years", help="Comma-separated list of Arizona legislative years")
    sync_parser.add_argument("--arkansas-years", help="Comma-separated list of Arkansas legislative years")
    sync_parser.add_argument("--california-years", help="Comma-separated list of California legislative years")
    sync_parser.add_argument("--georgia-years", help="Comma-separated list of Georgia legislative years")
    sync_parser.add_argument("--delaware-years", help="Comma-separated list of Delaware General Assembly start years")
    sync_parser.add_argument(
        "--district-of-columbia-years",
        help="Comma-separated list of District of Columbia council-period start years",
    )
    sync_parser.add_argument("--florida-years", help="Comma-separated list of Florida legislative years")
    sync_parser.add_argument("--hawaii-years", help="Comma-separated list of Hawaii legislative years")
    sync_parser.add_argument("--idaho-years", help="Comma-separated list of Idaho legislative years")
    sync_parser.add_argument("--indiana-years", help="Comma-separated list of Indiana legislative years")
    sync_parser.add_argument("--illinois-years", help="Comma-separated list of Illinois legislative years")
    sync_parser.add_argument("--north-dakota-years", help="Comma-separated list of North Dakota legislative years")
    sync_parser.add_argument("--iowa-years", help="Comma-separated list of Iowa legislative years")
    sync_parser.add_argument("--kentucky-years", help="Comma-separated list of Kentucky legislative years")
    sync_parser.add_argument("--louisiana-years", help="Comma-separated list of Louisiana legislative years")
    sync_parser.add_argument("--maine-years", help="Comma-separated list of Maine legislative session start years")
    sync_parser.add_argument("--maryland-years", help="Comma-separated list of Maryland legislative years")
    sync_parser.add_argument("--massachusetts-years", help="Comma-separated list of Massachusetts General Court start years")
    sync_parser.add_argument("--michigan-years", help="Comma-separated list of Michigan legislative years")
    sync_parser.add_argument("--washington-years", help="Comma-separated list of Washington legislative years")
    sync_parser.add_argument("--connecticut-years", help="Comma-separated list of Connecticut legislative years")
    sync_parser.add_argument("--new-mexico-years", help="Comma-separated list of New Mexico legislative years")
    sync_parser.add_argument("--nebraska-years", help="Comma-separated list of Nebraska legislative years")
    sync_parser.add_argument("--south-carolina-years", help="Comma-separated list of South Carolina legislative years")
    sync_parser.add_argument("--south-dakota-years", help="Comma-separated list of South Dakota legislative years")
    sync_parser.add_argument("--vermont-years", help="Comma-separated list of Vermont legislative years")
    sync_parser.add_argument("--utah-years", help="Comma-separated list of Utah legislative years")
    sync_parser.add_argument("--virginia-years", help="Comma-separated list of Virginia legislative years")
    sync_parser.add_argument("--rhode-island-years", help="Comma-separated list of Rhode Island legislative years")
    sync_parser.add_argument("--minnesota-years", help="Comma-separated list of Minnesota legislative years")
    sync_parser.add_argument("--missouri-years", help="Comma-separated list of Missouri legislative years")
    sync_parser.add_argument("--montana-years", help="Comma-separated list of Montana legislative years")
    sync_parser.add_argument("--nevada-years", help="Comma-separated list of Nevada legislative years")
    sync_parser.add_argument("--new-hampshire-years", help="Comma-separated list of New Hampshire legislative years")
    sync_parser.add_argument("--new-jersey-years", help="Comma-separated list of New Jersey session start years")
    sync_parser.add_argument("--new-york-years", help="Comma-separated list of New York legislative session start years")
    sync_parser.add_argument("--ohio-years", help="Comma-separated list of Ohio legislative years")
    sync_parser.add_argument("--west-virginia-years", help="Comma-separated list of West Virginia legislative years")
    sync_parser.add_argument("--colorado-years", help="Comma-separated list of Colorado legislative years")
    sync_parser.add_argument("--texas-years", help="Comma-separated list of Texas legislative years")
    sync_parser.add_argument("--oklahoma-years", help="Comma-separated list of Oklahoma legislative years")
    sync_parser.add_argument("--oregon-years", help="Comma-separated list of Oregon legislative years")
    sync_parser.add_argument("--pennsylvania-years", help="Comma-separated list of Pennsylvania session years")
    sync_parser.add_argument("--tennessee-years", help="Comma-separated list of Tennessee coverage years")
    sync_parser.add_argument("--mississippi-years", help="Comma-separated list of Mississippi legislative years")
    sync_parser.add_argument("--north-carolina-years", help="Comma-separated list of North Carolina session years")
    sync_parser.add_argument("--wisconsin-years", help="Comma-separated list of Wisconsin biennium years")
    sync_parser.add_argument("--federal-congresses", help="Comma-separated list of Congress numbers to sync")
    sync_parser.add_argument("--limit", type=int, help="Optional max number of bills to process")
    sync_parser.add_argument("--federal-limit", type=int, help="Optional max number of federal bills to process")
    sync_parser.add_argument(
        "--skip-interpretation",
        action="store_true",
        help="Skip Ollama plain-English generation during this run",
    )
    sync_parser.add_argument(
        "--skip-alaska",
        action="store_true",
        help="Skip the Alaska sync during this run",
    )
    sync_parser.add_argument(
        "--skip-kansas",
        action="store_true",
        help="Skip the Kansas sync during this run",
    )
    sync_parser.add_argument(
        "--skip-alabama",
        action="store_true",
        help="Skip the Alabama sync during this run",
    )
    sync_parser.add_argument(
        "--skip-arizona",
        action="store_true",
        help="Skip the Arizona sync during this run",
    )
    sync_parser.add_argument(
        "--skip-arkansas",
        action="store_true",
        help="Skip the Arkansas sync during this run",
    )
    sync_parser.add_argument(
        "--skip-california",
        action="store_true",
        help="Skip the California sync during this run",
    )
    sync_parser.add_argument(
        "--skip-georgia",
        action="store_true",
        help="Skip the Georgia sync during this run",
    )
    sync_parser.add_argument(
        "--skip-delaware",
        action="store_true",
        help="Skip the Delaware sync during this run",
    )
    sync_parser.add_argument(
        "--skip-connecticut",
        action="store_true",
        help="Skip the Connecticut sync during this run",
    )
    sync_parser.add_argument(
        "--skip-district-of-columbia",
        action="store_true",
        help="Skip the District of Columbia sync during this run",
    )
    sync_parser.add_argument(
        "--skip-florida",
        action="store_true",
        help="Skip the Florida sync during this run",
    )
    sync_parser.add_argument(
        "--skip-hawaii",
        action="store_true",
        help="Skip the Hawaii sync during this run",
    )
    sync_parser.add_argument(
        "--skip-idaho",
        action="store_true",
        help="Skip the Idaho sync during this run",
    )
    sync_parser.add_argument(
        "--skip-indiana",
        action="store_true",
        help="Skip the Indiana sync during this run",
    )
    sync_parser.add_argument(
        "--skip-illinois",
        action="store_true",
        help="Skip the Illinois sync during this run",
    )
    sync_parser.add_argument(
        "--skip-north-dakota",
        action="store_true",
        help="Skip the North Dakota sync during this run",
    )
    sync_parser.add_argument(
        "--skip-iowa",
        action="store_true",
        help="Skip the Iowa sync during this run",
    )
    sync_parser.add_argument(
        "--skip-kentucky",
        action="store_true",
        help="Skip the Kentucky sync during this run",
    )
    sync_parser.add_argument(
        "--skip-louisiana",
        action="store_true",
        help="Skip the Louisiana sync during this run",
    )
    sync_parser.add_argument(
        "--skip-maine",
        action="store_true",
        help="Skip the Maine sync during this run",
    )
    sync_parser.add_argument(
        "--skip-maryland",
        action="store_true",
        help="Skip the Maryland sync during this run",
    )
    sync_parser.add_argument(
        "--skip-massachusetts",
        action="store_true",
        help="Skip the Massachusetts sync during this run",
    )
    sync_parser.add_argument(
        "--skip-michigan",
        action="store_true",
        help="Skip the Michigan sync during this run",
    )
    sync_parser.add_argument(
        "--skip-washington",
        action="store_true",
        help="Skip the Washington sync during this run",
    )
    sync_parser.add_argument(
        "--skip-nebraska",
        action="store_true",
        help="Skip the Nebraska sync during this run",
    )
    sync_parser.add_argument(
        "--skip-south-carolina",
        action="store_true",
        help="Skip the South Carolina sync during this run",
    )
    sync_parser.add_argument(
        "--skip-south-dakota",
        action="store_true",
        help="Skip the South Dakota sync during this run",
    )
    sync_parser.add_argument(
        "--skip-vermont",
        action="store_true",
        help="Skip the Vermont sync during this run",
    )
    sync_parser.add_argument(
        "--skip-new-hampshire",
        action="store_true",
        help="Skip the New Hampshire sync during this run",
    )
    sync_parser.add_argument(
        "--skip-utah",
        action="store_true",
        help="Skip the Utah sync during this run",
    )
    sync_parser.add_argument(
        "--skip-virginia",
        action="store_true",
        help="Skip the Virginia sync during this run",
    )
    sync_parser.add_argument(
        "--skip-rhode-island",
        action="store_true",
        help="Skip the Rhode Island sync during this run",
    )
    sync_parser.add_argument(
        "--skip-minnesota",
        action="store_true",
        help="Skip the Minnesota sync during this run",
    )
    sync_parser.add_argument(
        "--skip-missouri",
        action="store_true",
        help="Skip the Missouri sync during this run",
    )
    sync_parser.add_argument(
        "--skip-montana",
        action="store_true",
        help="Skip the Montana sync during this run",
    )
    sync_parser.add_argument(
        "--skip-nevada",
        action="store_true",
        help="Skip the Nevada sync during this run",
    )
    sync_parser.add_argument(
        "--skip-new-jersey",
        action="store_true",
        help="Skip the New Jersey sync during this run",
    )
    sync_parser.add_argument(
        "--skip-new-york",
        action="store_true",
        help="Skip the New York sync during this run",
    )
    sync_parser.add_argument(
        "--skip-ohio",
        action="store_true",
        help="Skip the Ohio sync during this run",
    )
    sync_parser.add_argument(
        "--skip-west-virginia",
        action="store_true",
        help="Skip the West Virginia sync during this run",
    )
    sync_parser.add_argument(
        "--skip-wyoming",
        action="store_true",
        help="Skip the Wyoming sync during this run",
    )
    sync_parser.add_argument(
        "--skip-federal",
        action="store_true",
        help="Skip the federal sync during this run",
    )
    sync_parser.add_argument(
        "--skip-colorado",
        action="store_true",
        help="Skip the Colorado sync during this run",
    )
    sync_parser.add_argument(
        "--skip-texas",
        action="store_true",
        help="Skip the Texas sync during this run",
    )
    sync_parser.add_argument(
        "--skip-oklahoma",
        action="store_true",
        help="Skip the Oklahoma sync during this run",
    )
    sync_parser.add_argument(
        "--skip-oregon",
        action="store_true",
        help="Skip the Oregon sync during this run",
    )
    sync_parser.add_argument(
        "--skip-pennsylvania",
        action="store_true",
        help="Skip the Pennsylvania sync during this run",
    )
    sync_parser.add_argument(
        "--skip-tennessee",
        action="store_true",
        help="Skip the Tennessee sync during this run",
    )
    sync_parser.add_argument(
        "--skip-mississippi",
        action="store_true",
        help="Skip the Mississippi sync during this run",
    )
    sync_parser.add_argument(
        "--skip-north-carolina",
        action="store_true",
        help="Skip the North Carolina sync during this run",
    )
    sync_parser.add_argument(
        "--skip-wisconsin",
        action="store_true",
        help="Skip the Wisconsin sync during this run",
    )
    sync_parser.add_argument(
        "--skip-new-mexico",
        action="store_true",
        help="Skip the New Mexico sync during this run",
    )
    sync_parser.add_argument(
        "--skip-relationships",
        action="store_true",
        help="Skip the bill-pair relationship review during this run",
    )

    retag_parser = subparsers.add_parser("retag", help="Recompute bill tags and search text from stored data")
    retag_parser.add_argument("--state", default="wy", help="Jurisdiction code to retag, like wy or us")
    retag_parser.add_argument("--years", help="Comma-separated list of years or Congress numbers to retag")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = get_settings()

    if args.command == "init-db":
        init_db()
        print(f"Initialized database at {settings.database_path}")
        return

    if args.command == "sync":
        years = None
        if args.years:
            years = [int(item.strip()) for item in args.years.split(",") if item.strip()]
        alaska_years = None
        if args.alaska_years:
            alaska_years = [int(item.strip()) for item in args.alaska_years.split(",") if item.strip()]
        kansas_years = None
        if args.kansas_years:
            kansas_years = [int(item.strip()) for item in args.kansas_years.split(",") if item.strip()]
        alabama_years = None
        if args.alabama_years:
            alabama_years = [int(item.strip()) for item in args.alabama_years.split(",") if item.strip()]
        arizona_years = None
        if args.arizona_years:
            arizona_years = [int(item.strip()) for item in args.arizona_years.split(",") if item.strip()]
        arkansas_years = None
        if args.arkansas_years:
            arkansas_years = [int(item.strip()) for item in args.arkansas_years.split(",") if item.strip()]
        california_years = None
        if args.california_years:
            california_years = [int(item.strip()) for item in args.california_years.split(",") if item.strip()]
        georgia_years = None
        if args.georgia_years:
            georgia_years = [int(item.strip()) for item in args.georgia_years.split(",") if item.strip()]
        delaware_years = None
        if args.delaware_years:
            delaware_years = [int(item.strip()) for item in args.delaware_years.split(",") if item.strip()]
        district_of_columbia_years = None
        if args.district_of_columbia_years:
            district_of_columbia_years = [
                int(item.strip()) for item in args.district_of_columbia_years.split(",") if item.strip()
            ]
        florida_years = None
        if args.florida_years:
            florida_years = [int(item.strip()) for item in args.florida_years.split(",") if item.strip()]
        hawaii_years = None
        if args.hawaii_years:
            hawaii_years = [int(item.strip()) for item in args.hawaii_years.split(",") if item.strip()]
        idaho_years = None
        if args.idaho_years:
            idaho_years = [int(item.strip()) for item in args.idaho_years.split(",") if item.strip()]
        indiana_years = None
        if args.indiana_years:
            indiana_years = [int(item.strip()) for item in args.indiana_years.split(",") if item.strip()]
        illinois_years = None
        if args.illinois_years:
            illinois_years = [int(item.strip()) for item in args.illinois_years.split(",") if item.strip()]
        north_dakota_years = None
        if args.north_dakota_years:
            north_dakota_years = [int(item.strip()) for item in args.north_dakota_years.split(",") if item.strip()]
        iowa_years = None
        if args.iowa_years:
            iowa_years = [int(item.strip()) for item in args.iowa_years.split(",") if item.strip()]
        kentucky_years = None
        if args.kentucky_years:
            kentucky_years = [int(item.strip()) for item in args.kentucky_years.split(",") if item.strip()]
        louisiana_years = None
        if args.louisiana_years:
            louisiana_years = [int(item.strip()) for item in args.louisiana_years.split(",") if item.strip()]
        maine_years = None
        if args.maine_years:
            maine_years = [int(item.strip()) for item in args.maine_years.split(",") if item.strip()]
        maryland_years = None
        if args.maryland_years:
            maryland_years = [int(item.strip()) for item in args.maryland_years.split(",") if item.strip()]
        massachusetts_years = None
        if args.massachusetts_years:
            massachusetts_years = [int(item.strip()) for item in args.massachusetts_years.split(",") if item.strip()]
        michigan_years = None
        if args.michigan_years:
            michigan_years = [int(item.strip()) for item in args.michigan_years.split(",") if item.strip()]
        washington_years = None
        if args.washington_years:
            washington_years = [int(item.strip()) for item in args.washington_years.split(",") if item.strip()]
        connecticut_years = None
        if args.connecticut_years:
            connecticut_years = [int(item.strip()) for item in args.connecticut_years.split(",") if item.strip()]
        new_mexico_years = None
        if args.new_mexico_years:
            new_mexico_years = [int(item.strip()) for item in args.new_mexico_years.split(",") if item.strip()]
        nebraska_years = None
        if args.nebraska_years:
            nebraska_years = [int(item.strip()) for item in args.nebraska_years.split(",") if item.strip()]
        south_carolina_years = None
        if args.south_carolina_years:
            south_carolina_years = [int(item.strip()) for item in args.south_carolina_years.split(",") if item.strip()]
        south_dakota_years = None
        if args.south_dakota_years:
            south_dakota_years = [int(item.strip()) for item in args.south_dakota_years.split(",") if item.strip()]
        vermont_years = None
        if args.vermont_years:
            vermont_years = [int(item.strip()) for item in args.vermont_years.split(",") if item.strip()]
        utah_years = None
        if args.utah_years:
            utah_years = [int(item.strip()) for item in args.utah_years.split(",") if item.strip()]
        virginia_years = None
        if args.virginia_years:
            virginia_years = [int(item.strip()) for item in args.virginia_years.split(",") if item.strip()]
        rhode_island_years = None
        if args.rhode_island_years:
            rhode_island_years = [int(item.strip()) for item in args.rhode_island_years.split(",") if item.strip()]
        minnesota_years = None
        if args.minnesota_years:
            minnesota_years = [int(item.strip()) for item in args.minnesota_years.split(",") if item.strip()]
        missouri_years = None
        if args.missouri_years:
            missouri_years = [int(item.strip()) for item in args.missouri_years.split(",") if item.strip()]
        montana_years = None
        if args.montana_years:
            montana_years = [int(item.strip()) for item in args.montana_years.split(",") if item.strip()]
        nevada_years = None
        if args.nevada_years:
            nevada_years = [int(item.strip()) for item in args.nevada_years.split(",") if item.strip()]
        new_hampshire_years = None
        if args.new_hampshire_years:
            new_hampshire_years = [int(item.strip()) for item in args.new_hampshire_years.split(",") if item.strip()]
        new_jersey_years = None
        if args.new_jersey_years:
            new_jersey_years = [int(item.strip()) for item in args.new_jersey_years.split(",") if item.strip()]
        new_york_years = None
        if args.new_york_years:
            new_york_years = [int(item.strip()) for item in args.new_york_years.split(",") if item.strip()]
        ohio_years = None
        if args.ohio_years:
            ohio_years = [int(item.strip()) for item in args.ohio_years.split(",") if item.strip()]
        west_virginia_years = None
        if args.west_virginia_years:
            west_virginia_years = [int(item.strip()) for item in args.west_virginia_years.split(",") if item.strip()]
        colorado_years = None
        if args.colorado_years:
            colorado_years = [int(item.strip()) for item in args.colorado_years.split(",") if item.strip()]
        texas_years = None
        if args.texas_years:
            texas_years = [int(item.strip()) for item in args.texas_years.split(",") if item.strip()]
        oklahoma_years = None
        if args.oklahoma_years:
            oklahoma_years = [int(item.strip()) for item in args.oklahoma_years.split(",") if item.strip()]
        oregon_years = None
        if args.oregon_years:
            oregon_years = [int(item.strip()) for item in args.oregon_years.split(",") if item.strip()]
        pennsylvania_years = None
        if args.pennsylvania_years:
            pennsylvania_years = [int(item.strip()) for item in args.pennsylvania_years.split(",") if item.strip()]
        tennessee_years = None
        if args.tennessee_years:
            tennessee_years = [int(item.strip()) for item in args.tennessee_years.split(",") if item.strip()]
        mississippi_years = None
        if args.mississippi_years:
            mississippi_years = [int(item.strip()) for item in args.mississippi_years.split(",") if item.strip()]
        north_carolina_years = None
        if args.north_carolina_years:
            north_carolina_years = [int(item.strip()) for item in args.north_carolina_years.split(",") if item.strip()]
        wisconsin_years = None
        if args.wisconsin_years:
            wisconsin_years = [int(item.strip()) for item in args.wisconsin_years.split(",") if item.strip()]
        federal_congresses = None
        if args.federal_congresses:
            federal_congresses = [int(item.strip()) for item in args.federal_congresses.split(",") if item.strip()]

        summary: dict[str, object] = {}
        if not args.skip_alaska:
            summary["alaska"] = asdict(
                sync_alaska(
                    years=alaska_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_kansas:
            summary["kansas"] = asdict(
                sync_kansas(
                    years=kansas_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_wyoming:
            summary["wyoming"] = asdict(
                sync_wyoming(
                    years=years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    skip_relationships=args.skip_relationships,
                    logger=print,
                )
            )
        if not args.skip_alabama:
            summary["alabama"] = asdict(
                sync_alabama(
                    years=alabama_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_arizona:
            summary["arizona"] = asdict(
                sync_arizona(
                    years=arizona_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_arkansas:
            summary["arkansas"] = asdict(
                sync_arkansas(
                    years=arkansas_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_california:
            summary["california"] = asdict(
                sync_california(
                    years=california_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_georgia:
            summary["georgia"] = asdict(
                sync_georgia(
                    years=georgia_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_delaware:
            summary["delaware"] = asdict(
                sync_delaware(
                    years=delaware_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_connecticut:
            summary["connecticut"] = asdict(
                sync_connecticut(
                    years=connecticut_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_district_of_columbia:
            summary["district_of_columbia"] = asdict(
                sync_district_of_columbia(
                    years=district_of_columbia_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_virginia:
            summary["virginia"] = asdict(
                sync_virginia(
                    years=virginia_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_florida:
            summary["florida"] = asdict(
                sync_florida(
                    years=florida_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_hawaii:
            summary["hawaii"] = asdict(
                sync_hawaii(
                    years=hawaii_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_idaho:
            summary["idaho"] = asdict(
                sync_idaho(
                    years=idaho_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_indiana:
            summary["indiana"] = asdict(
                sync_indiana(
                    years=indiana_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_illinois:
            summary["illinois"] = asdict(
                sync_illinois(
                    years=illinois_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_north_dakota:
            summary["north_dakota"] = asdict(
                sync_north_dakota(
                    years=north_dakota_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_iowa:
            summary["iowa"] = asdict(
                sync_iowa(
                    years=iowa_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_kentucky:
            summary["kentucky"] = asdict(
                sync_kentucky(
                    years=kentucky_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_louisiana:
            summary["louisiana"] = asdict(
                sync_louisiana(
                    years=louisiana_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_maine:
            summary["maine"] = asdict(
                sync_maine(
                    years=maine_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_maryland:
            summary["maryland"] = asdict(
                sync_maryland(
                    years=maryland_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_massachusetts:
            summary["massachusetts"] = asdict(
                sync_massachusetts(
                    years=massachusetts_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_michigan:
            summary["michigan"] = asdict(
                sync_michigan(
                    years=michigan_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_washington:
            summary["washington"] = asdict(
                sync_washington(
                    years=washington_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_nebraska:
            summary["nebraska"] = asdict(
                sync_nebraska(
                    years=nebraska_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_south_carolina:
            summary["south_carolina"] = asdict(
                sync_south_carolina(
                    years=south_carolina_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_south_dakota:
            summary["south_dakota"] = asdict(
                sync_south_dakota(
                    years=south_dakota_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_vermont:
            summary["vermont"] = asdict(
                sync_vermont(
                    years=vermont_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_utah:
            summary["utah"] = asdict(
                sync_utah(
                    years=utah_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_rhode_island:
            summary["rhode_island"] = asdict(
                sync_rhode_island(
                    years=rhode_island_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_minnesota:
            summary["minnesota"] = asdict(
                sync_minnesota(
                    years=minnesota_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_missouri:
            summary["missouri"] = asdict(
                sync_missouri(
                    years=missouri_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_montana:
            summary["montana"] = asdict(
                sync_montana(
                    years=montana_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_nevada:
            summary["nevada"] = asdict(
                sync_nevada(
                    years=nevada_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_new_hampshire:
            summary["new_hampshire"] = asdict(
                sync_new_hampshire(
                    years=new_hampshire_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_new_jersey:
            summary["new_jersey"] = asdict(
                sync_new_jersey(
                    years=new_jersey_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_new_york:
            summary["new_york"] = asdict(
                sync_new_york(
                    years=new_york_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_ohio:
            summary["ohio"] = asdict(
                sync_ohio(
                    years=ohio_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_west_virginia:
            summary["west_virginia"] = asdict(
                sync_west_virginia(
                    years=west_virginia_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_colorado:
            summary["colorado"] = asdict(
                sync_colorado(
                    years=colorado_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_texas:
            summary["texas"] = asdict(
                sync_texas(
                    years=texas_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_oklahoma:
            summary["oklahoma"] = asdict(
                sync_oklahoma(
                    years=oklahoma_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_oregon:
            summary["oregon"] = asdict(
                sync_oregon(
                    years=oregon_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_pennsylvania:
            summary["pennsylvania"] = asdict(
                sync_pennsylvania(
                    years=pennsylvania_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_tennessee:
            summary["tennessee"] = asdict(
                sync_tennessee(
                    years=tennessee_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_mississippi:
            summary["mississippi"] = asdict(
                sync_mississippi(
                    years=mississippi_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_north_carolina:
            summary["north_carolina"] = asdict(
                sync_north_carolina(
                    years=north_carolina_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_wisconsin:
            summary["wisconsin"] = asdict(
                sync_wisconsin(
                    years=wisconsin_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_new_mexico:
            summary["new_mexico"] = asdict(
                sync_new_mexico(
                    years=new_mexico_years,
                    limit=args.limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        if not args.skip_federal:
            summary["federal"] = asdict(
                sync_federal(
                    congresses=federal_congresses,
                    limit=args.federal_limit,
                    skip_interpretation=args.skip_interpretation,
                    logger=print,
                )
            )
        print(json.dumps(summary, indent=2))
        return

    if args.command == "retag":
        years = None
        if args.years:
            years = [int(item.strip()) for item in args.years.split(",") if item.strip()]
        summary = retag_bills(
            state=str(args.state or "wy").strip().lower(),
            years=years,
            logger=print,
        )
        payload = asdict(summary) if is_dataclass(summary) else summary
        print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
