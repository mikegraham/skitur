from skitur.cli import main, parse_args

args = parse_args()
main(args.gpx_file, args.output,
     generate_plots=not args.no_plots, resample=not args.no_resample)
