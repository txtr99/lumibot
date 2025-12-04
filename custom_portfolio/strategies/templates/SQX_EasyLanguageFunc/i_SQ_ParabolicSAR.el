{ Search Tag: WA-Parabolic SAR }

{ This code plots Welles Wilder's parabolic indicator. }

inputs:
	double AfStep( 0.02 ) [DisplayName = "AfStep", ToolTip = 
	 "Acceleration Factor Step.  Enter the step amount for the parabolic acceleration factor."],
	double AfLimit( 0.2 ) [DisplayName = "AfLimit", ToolTip = 
	 "Acceleration Factor Limit.  Enter the maximum acceleration factor for the parabolic calculation."],
	bool UsePlotColoring( true ) [DisplayName = "UsePlotColoring", ToolTip = 
	 "Enter true to use a different plot color if the SAR value is above or below price.  Enter false to use the same plot color."],
	int PriceAbvParabColor( Cyan ) [DisplayName = "PriceAbvParabColor", ToolTip = 
	 "Price Above Parabolic Color.  Enter the color for plot when Price is greater than the SAR value."],
	int PriceBlwParabColor( Magenta )
	 [DisplayName = "PriceBlwParabColor", ToolTip = 
	 "Price Above Parabolic Color.  Enter the color for plot when Price is less than the SAR value."],
	bool ColorCellBGOnAlert( true ) [DisplayName = "ColorCellBGOnAlert", ToolTip = 
	 "Color Cell Background On Alert.  Enter true to color RadarScreen cell background when alert occurs;  enter false to not color cell background."],
	int BackgroundColorAlertCell( DarkGray )
	 [DisplayName = "BackgroundColorAlertCell", ToolTip = 
	 "Enter the color to use for RadarScreen cell background when alert occurs."];
	 
variables:
	intrabarpersist bool InAChart( false ),
	int ReturnValue( 0 ),
	double oParCl( 0 ),
	double oParOp( 0 ),
	double oPosition( 0 ),
	double oTransition( 0 ),
	int CrossBarsAgo( 0 ),
	intrabarpersist bool Alerting( false ),
	AlertStr( "" );

once
begin	
	InAChart = GetAppInfo( aiApplicationType ) = cChart;
end;
	
ReturnValue = ParabolicSAR( AfStep, AfLimit, oParCl, oParOp, oPosition, 
 oTransition );

{ 
track the number of bars since the last crossover;  if oTransition is non-zero
then there is a crossover on the current bar, so set CrossBarsAgo to 0 when this
occurs, otherwise, increment CrossBarsAgo by 1 on every bar
}
if oTransition <> 0 then
	CrossBarsAgo = 0
else 
	CrossBarsAgo += 1;
	
Plot1( oParOp, !( "ParOp" ) );

{ 
if the application is something other than Charting, like RadarScreen, then
plot the the number of bars ago that the last cross	of the parabolic occurred;
this value is not plotted in Charting since it is on a different scale than the 
parabolic itself
} 		
if InAChart = false then 
	Plot2( CrossBarsAgo, !( "CrossBarsAgo" ) );

if UsePlotColoring then
begin
	if oPosition = 1 then
	begin
		SetPlotColor( 1, PriceAbvParabColor );
		SetPlotColor( 2, PriceAbvParabColor );
	end
	else
	begin
		SetPlotColor( 1, PriceBlwParabColor );	
		SetPlotColor( 2, PriceBlwParabColor );
	end;
end;

{ alert criteria }
if oTransition = 1 then 
begin
	Alerting = true;
	Alert( !( "Bullish reversal" ) );
end
else if oTransition = -1 then 
begin
	Alerting = true;
	Alert( !( "Bearish reversal" ) );
end
else
	Alerting = false;
	
{ Alternate alert criteria to write next reversal level to alert dialog box }
{
AlertStr = NumToStr( oParOp, 2 );

if oPosition = 1 then
	begin
	Alerting = true;
	Alert( !( "At next bar, stop and reverse current long position at " )
	 + AlertStr );
	end 
else if oPosition = -1 then
	begin
	Alerting = true;	
	Alert( !( "At next bar, stop and reverse current short position at " ) 
	 + AlertStr );
	end;
}

{ cell background coloring, see input ColorCellBGOnAlert, above }
if ColorCellBGOnAlert and Alerting then	
	SetPlotBGColor( 2, BackgroundColorAlertCell );


{ ** Copyright © TradeStation Technologies, Inc.  All Rights Reserved ** 
  ** TradeStation reserves the right to modify or overwrite this analysis technique 
     with each release. ** }
